import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import sys
sys.path.append("../")

import warnings
warnings.filterwarnings('ignore')

from kats.consts import TimeSeriesData

try: # If running on Jupyter
    air_passengers_df = pd.read_csv("../kats/data/air_passengers.csv")
except FileNotFoundError: # If running on colab
    air_passengers_df = pd.read_csv("air_passengers.csv")

"""
Note: If the column holding the time values is not called "time", you will want to specify 
the name of this column using the time_col_name parameter in the TimeSeriesData constructor.
"""
air_passengers_df.columns = ["time", "value"]
print(air_passengers_df.head())

