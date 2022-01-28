#!/usr/bin/python3
########################################################################################
# grid.py - Grid-profile generation module.                                            #
#                                                                                      #
# Author: Phil Sandwell                                                                #
# Copyright: Phil Sandwell, 2018                                                       #
# License: Open source                                                                 #
# Most recent update: 14/07/2021                                                       #
#                                                                                      #
# For more information, please email:                                                  #
#     philip.sandwell@gmail.com                                                        #
########################################################################################
"""
grid.py - The grid-generation module for CLOVER.

This module generates grid-availability profiles for CLOVER.

"""

from logging import Logger
from typing import Dict

import pandas as pd  # pylint: disable=import-error

from .__utils__ import get_intermittent_supply_status

__all__ = ("get_lifetime_grid_status",)


def get_lifetime_grid_status(
    generation_directory: str, grid_times: pd.DataFrame, logger: Logger, max_years: int
) -> Dict[str, pd.DataFrame]:
    """
    Calculates, and saves, the grid-availability profiles of all input types.

    Inputs:
        - generation_directory:
            The directory in which auto-generated files should be saved.
        - grid_times:
            Grid inputs information, read from the grid-inputs file.
        - logger:
            The logger to use for the run.
        - max_years:
            The maximum number of years for which the simulation should run.

    Outputs:
        - grid_profiles:
            A dictionary mapping the grid name to the grid profile.

    """

    return get_intermittent_supply_status(
        generation_directory, "grid", logger, max_years, grid_times
    )


#     #%%
#     def change_grid_coverage(self, grid_type="bahraich", hours=12):
#         grid_profile = self.grid_times[grid_type]
#         baseline_hours = np.sum(grid_profile)
#         new_profile = pd.DataFrame([0] * 24)
#         for hour in range(24):
#             m = interp1d([0, baseline_hours, 24], [0, grid_profile[hour], 1])
#             new_profile.iloc[hour] = m(hours).round(3)
#         new_profile.columns = [grid_type + "_" + str(hours)]
#         return new_profile

#     def save_grid_coverage(self, grid_type="bahraich", hours=12):
#         new_profile = self.change_grid_coverage(grid_type, hours)
#         new_profile_name = grid_type + "_" + str(hours)
#         output = self.grid_times
#         if new_profile_name in output.columns:
#             output[new_profile_name] = new_profile
#         else:
#             output = pd.concat([output, new_profile], axis=1)
#         output.to_csv(self.generation_filepath + "Grid inputs.csv")
