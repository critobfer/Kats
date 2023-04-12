# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Ensemble models Bates & Granger individual models

Assume we have k base models, after we make forecasts with each individual
model, Computes forecast combination weights according to the approach by 
Bates and Granger (1969) and produces a forecast.
"""
import logging
import sys
from multiprocessing import cpu_count, Pool
from typing import Any, cast, Dict, List, Optional, Type, Union

import numpy as np
import pandas as pd
from kats.consts import Params, TimeSeriesData
from kats.models.ensemble import ensemble
from kats.models.ensemble.ensemble import BASE_MODELS, EnsembleParams
from kats.models.model import Model
from kats.utils.backtesters import BackTesterSimple, BackTesterExpandingWindow, BackTesterRollingWindow, BackTesterFixedWindow

class BatesGrangerEnsemble(ensemble.BaseEnsemble):
    """Bates & Granger ensemble model class

    Attributes:
        data: the input time series data as in :class:`kats.consts.TimeSeriesData`
        params: the model parameter class in Kats
    """
    back_method: Optional[str] = "simple"
    freq: Optional[str] = None
    errors: Optional[Dict[str, Any]] = None
    weights: Optional[Dict[str, Any]] = None
    fcst_weighted: Optional[Union[pd.DataFrame, pd.Series]] = None
    fcst_dates: Optional[pd.DatetimeIndex] = None
    dates: Optional[pd.DatetimeIndex] = None
    fcst_df: Optional[pd.DataFrame] = None

    def __init__(self, data: TimeSeriesData, params: EnsembleParams) -> None:
        self.data = data
        self.params = params
        # self.back_method = back_method
        if not isinstance(self.data.value, pd.Series):
            msg = "Only support univariate time series, but get {type}.".format(
                type=type(self.data.value)
            )
            logging.error(msg)
            raise ValueError(msg)

    def _backtester_single(
        self,
        params: Params,
        # pyre-fixme[24]: Generic type `Model` expects 1 type parameter.
        model_class: Type[Model],
        err_method: str = "mse"
    ) -> float:
        """Private method to run all backtesting process

        Args:
            params: Kats model parameters
            model_class: Untyped. Defines type of model
            train_percentage: float. Percentage of data used for training
            test_percentage: float. Percentage of data used for testing
            error_method: list of strings indicating which errors to calculate
                we currently support "mape", "smape", "mae", "mase", "mse", "rmse"

        Returns:
            float, the backtesting error
        """
        back_method = self.back_method
        if back_method not in ["simple","expanding","rolling","fixed"]:
            return print("This method are not available, chooose  simple, expanding, rolling or fixed")
        
        if back_method == "simple":
            bt = BackTesterSimple(
                [err_method],
                self.data,
                params,
                train_percentage = 80 ,
                test_percentage = 20,
                model_class = model_class,
            )
        if back_method == "expanding":
            bt = BackTesterExpandingWindow(
                [err_method],
                self.data,
                params,
                start_train_percentage = 40,
                end_train_percentage = 40, # it is not use, but we must define it
                expanding_steps = 10, #nfold
                test_percentage = 10,
                model_class = model_class,
            )
        if back_method == "rolling":
            bt = BackTesterRollingWindow(
                [err_method],
                self.data,
                params,
                train_percentage = 40,
                test_percentage = 10,
                sliding_steps = 10,
                model_class = model_class,
            )
            
        if back_method == "fixed":
            bt = BackTesterFixedWindow(
                [err_method],
                self.data,
                params,
                train_percentage = 75,
                test_percentage = 15,
                window_percentage = 10,
                model_class = model_class,
            )
            
        bt.run_backtest()
        return bt.get_error_value(err_method)

    def _backtester_all(self, err_method: str = "mse") -> Dict[str, Any]:
        """Private method to run all backtesting process

        Args:
            error_method: list of strings indicating which errors to calculate
                we currently support "mape", "smape", "mae", "mase", "mse", "rmse"

        Returns:
            Dict of errors from each model
        """

        num_process = min(len(BASE_MODELS.keys()), (cpu_count() - 1) // 2)
        if num_process < 1:
            num_process = 1
        pool = Pool(processes=(num_process), maxtasksperchild=1000)
        backtesters = {}
        for model in self.params.models:
            backtesters[model.model_name] = pool.apply_async(
                self._backtester_single,
                args=(model.model_params, BASE_MODELS[model.model_name.lower()]),
                kwds={"err_method": err_method},
            )
        pool.close()
        pool.join()
        self.errors = {model: res.get() for model, res in backtesters.items()}
        original_weights = {
            model: 1/(err**2 + sys.float_info.epsilon) # El mse
            for model, err in self.errors.items()
        }
        self.weights = {
            model: err / sum(original_weights.values()) 
            # El peso original es el mse, si lo dividimos por la suma de los pesos tenemos el valor final
            for model, err in original_weights.items()
        }
        return self.weights

    # pyre-fixme[14]: `predict` overrides method defined in `Model` inconsistently.
    # pyre-fixme[2]: Parameter must be annotated.
    # pyre-fixme[15]: `predict` overrides method defined in `Model` inconsistently.
    def predict(self, steps: int, **kwargs) -> pd.DataFrame:
        """Predict method of weighted average ensemble model

        Args:
            steps: the length of forecasting horizon

        Returns:
            forecasting results as in pd.DataFrame
        """
        # keep these in kwargs to pass to _predict_all.
        self.freq = freq = kwargs.get("freq", "D")
        err_method = kwargs.get("err_method", "mse")
        # calculate the weights
        self._backtester_all(err_method=err_method)

        # fit model with all available time series
        pred_dict = self._predict_all(steps, **kwargs)

        fcst_all = pd.concat(
            # pyre-fixme[16]: `Model` has no attribute `fcst`.
            [x.fcst.reset_index(drop=True) for x in pred_dict.values()],
            axis=1,
            copy=False,
        )
        fcst_all.columns = cast(List[str], pred_dict.keys()) 
        # Lista de string y los modelos(elemento),lo convierte en este formato
        weights = self.weights 
        assert weights is not None #Si es None ==> AssertionError
        # PREDICCIÓN FINAL:
        self.fcst_weighted = fcst_all.dot(np.array(list(weights.values()))) # Producto matricial

        # create future dates
        last_date = self.data.time.max()
        dates = pd.date_range(start=last_date, periods=steps + 1, freq=freq)
        dates = dates[dates != last_date]
        self.fcst_dates = dates.to_pydatetime()
        self.dates = dates[dates != last_date]
        self.fcst_df = fcst_df = pd.DataFrame(
            {"time": self.dates, "fcst": self.fcst_weighted}, copy=False
        )

        logging.debug("Return forecast data: {fcst_df}")
        return fcst_df

    def __str__(self) -> str:
        """Get default parameter search space for the Bates & Gates ensemble model

        Args:
            None

        Returns:
            Model name as a string
        """
        return "Bates & Granger Ensemble"
