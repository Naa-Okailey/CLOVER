#!/usr/bin/python3
########################################################################################
# __utils__.py - Profile-generation utility module.                                    #
#                                                                                      #
# Author(s): Phil Sandwell, Ben Winchester                                             #
# Copyright: Phil Sandwell, 2021                                                       #
# Date created: 11/08/2021                                                             #
# License: Open source                                                                 #
# Most recent update: 14/07/2021                                                       #
#                                                                                      #
# Additional credits:                                                                  #
#     Iain Staffell, Stefan Pfenninger & Scot Wheeler                                  #
# For more information, please email:                                                  #
#     philip.sandwell@gmail.com                                                        #
########################################################################################
"""
__utils__.py - The profile-generation utility module for CLOVER.

This module fetches profiles from renewables.ninja, parses them and saves them for use
locally within CLOVER. The profiles that are fetched are determined by the information
that is passed in to the module.

"""

import enum
import json
import math
import os
import random
import threading
import time

from json.decoder import JSONDecodeError
from logging import Logger
from typing import Any, Dict, Union

import numpy as np  # pylint: disable=import-error
import pandas as pd  # pylint: disable=import-error
import requests  # pylint: disable=import-error

from tqdm import tqdm  # pylint: disable=import-error

from ..__utils__ import (
    BColours,
    InternalError,
    get_logger,
    Location,
    RenewablesNinjaError,
)

__all__ = (
    "BaseRenewablesNinjaThread",
    "TOKEN",
    "total_profile_output",
)


# Api base:
#   The base API name of renewables.ninja.
API_BASE = "https://www.renewables.ninja/api/"

# Feb 29:
#   Used to remove leap-year information.
FEB_29: int = (31 + 28) * 24

# Renewables.ninja sleep time:
#   To avoid being locked out of the renewables.ninja API, it is necessary for CLOVER to
#   sleep between requests. The time taken for this, in seconds, is set below.
RENEWABLES_NINJA_SLEEP_TIME = 12

# Token:
#   Keyword used when parsing the generation token.
TOKEN = "token"


class SolarDataType(enum.Enum):
    """
    Denotes the types of solar data being stored.

    - DIFFUSE_IRRADIANCE:
        Denotes the diffuse irradiance striking a panel.

    - DIRECT_IRRADIANCE:
        Denotes the direct irradiance striking a panel.

    - ELECTRICITY:
        Denotes the electricity output information generated by renewables.ninja.

    - TEMPERATURE:
        Denotes the surface temperature in degrees celcius.

    - TOTAL_IRRADIANCE
        Denotes the total irradiance striking the panel.

    """

    DIFFUSE_IRRADIANCE = "irradiance_diffuse"
    DIRECT_IRRADIANCE = "irradiance_direct"
    ELECTRICITY = "electricity"
    TEMPERATURE = "temperature"
    TOTAL_IRRADIANCE = "irradiance_total"


def _get_profile_from_rn(
    authorisation_token: str,
    logger: Logger,
    renewables_ninja_keyword: str,
    renewables_ninja_params: Dict[str, Any],
    year: int = 2014,
) -> pd.DataFrame:
    """
    Gets data from Renewables.ninja for a given year (kW/kWp) in UTC time

    Credit:
        Renewables.ninja, API interface and all data accessed by this function by Iain
            Staffell & Stefan Pfenninger.
        Python code from:
            https://www.renewables.ninja/documentation/api/python-example
        Cite these papers in your documents!
            - S. Pfenninger and I. Staffell, 2016. Long-term patterns of European PV
              output using 30 years of validated hourly reanalysis and satellite data.
              Energy, 114, 1251–1265.
            - I. Staffell and S. Pfenninger, 2016. Using Bias-Corrected Reanalysis to
              Simulate Current and Future Wind Power Output. Energy, 114, 1224–1239.
        Adapted from code by Scot Wheeler

    Inputs:
        - authorisation_token:
            The token to use when accessing the renewables.ninja API. For more
            information on this token, see the User Guide contained within the CLOVER
            repository or contact either the CLOVER or renewables.ninja development
            teams.
        - location:
            The location currently being considered.
        - logger:
            The logger to use for the run.
        - renewables_ninja_keyword:
            The name to use when calling the renewables.ninja API.
        - renewables_ninja_params:
            The parameters to use when calling the renewables.ninja API.
        - year:
            The year for which to fetch data, valid values are from 2000-2020 inclusive.

    Outputs:
        PV output data in kW/kWp in UTC time

    Notes:
        Data produced is not in local time.

    """

    # Access information
    session = requests.session()
    url = f"{API_BASE}data/{renewables_ninja_keyword}"
    try:
        session.headers = requests.structures.CaseInsensitiveDict(
            {"Authorization": f"Token {authorisation_token}"}
        )
    except TypeError as e:  # pylint: disable=invalid-name
        logger.error(
            "The token specified was of the incorrect type. Check the generation "
            "inputs file: %s",
            str(e),
        )
        raise

    # Gets some data from input file
    renewables_ninja_params.update(
        {
            "date_from": f"{year}-01-01",
            "date_to": f"{year}-12-31",
            "format": "json",
            "header": "true",
            # Metadata and raw data now supported by different function in API
            #            'metadata': False,
            #            'raw': False
        }
    )
    logger.info(
        "Calling renewables Ninja:\n  url: %s\n  headers: %s\n  params: %s",
        url,
        session.headers,
        renewables_ninja_params,
    )
    session_url = session.get(url, params=renewables_ninja_params)

    # Parse JSON to get a pandas.DataFrame
    try:
        parsed_response = json.loads(session_url.text)
    except JSONDecodeError as e:  # pylint: disable=invalid-name
        logger.error(
            "%sFailed to parse renewables.ninja data. Check that you correctly specified "
            "your API key: %s%s",
            BColours.fail,
            str(e),
            BColours.endc,
        )
        logger.info("Session text: %s", session_url.text)
        raise RenewablesNinjaError() from None

    data_frame: pd.DataFrame = pd.DataFrame(
        pd.read_json(json.dumps(parsed_response["data"]), orient="index")
    )
    data_frame = data_frame.reset_index(drop=True)

    # Remove leap days
    if year % 4 == 0:
        logger.debug("Dataframe:\n%s", data_frame)
        data_frame = data_frame.drop(list(range(FEB_29, FEB_29 + 24)))  # type: ignore
        data_frame = data_frame.reset_index(drop=True)

    # Remove empty rows from the dataframe.
    return data_frame


def _get_profile_local_time(
    data_utc: pd.DataFrame, time_difference: float = 0
) -> pd.DataFrame:
    """
    Converts data from Renewables.ninja (kW/kWp in UTC time) to user-defined local time.

    Inputs:
        - data_utc
            Profile data in UTC.
        - time_difference
            The desired time zone difference.

    Outputs:
        PV output data (kW/kWp) in local time

    """

    #   Round time difference to nearest hour (NB India, Nepal etc. do not do this)
    time_difference = round(time_difference)

    # East of Greenwich
    if time_difference > 0:
        splits = np.split(data_utc, [len(data_utc) - time_difference])
        data_local: pd.DataFrame = pd.concat([splits[1], splits[0]], ignore_index=True)
    # West of Greenwich
    elif time_difference < 0:
        splits = np.split(data_utc, [abs(time_difference)])
        data_local = pd.concat([splits[1], splits[0]], ignore_index=True)
    # No time difference, included for completeness
    else:
        data_local = data_utc

    return data_local


def _get_profile_output(
    authorisation_token: str,
    location: Location,
    logger: Logger,
    renewables_ninja_keyword: str,
    renewables_ninja_params: Dict[str, Any],
    gen_year: int = 2014,
) -> pd.DataFrame:
    """
    Generates data from Renewables Ninja and returns it in a DataFrame.

    Inputs:
        - location:
            The location currently being considered.
        - logger:
            The logger to use for the run.
        - generation_inputs:
            The generation inputs, extracted from the input file.
        - gen_year
            The year for which to fetch the data.

    Outputs:
        - The generated data for the year.

    """

    # Get output in local time for the given year
    local_time_output = _get_profile_local_time(
        _get_profile_from_rn(
            authorisation_token,
            logger,
            renewables_ninja_keyword,
            renewables_ninja_params,
            gen_year,
        ),
        time_difference=float(location.time_difference),
    )

    return local_time_output


def _save_profile_output(
    filepath: str,
    gen_year: int,
    logger: Logger,
    profile: pd.DataFrame,
) -> None:
    """
    Saves PV generation data as a named .csv file in the location generation file.

    Inputs:
        - filepath:
            The path to save the file to, including the directory in which the data file
            should be saved and the filename to use.
        - gen_year:
            The year for which the data was generated.
        - logger:
            The logger to use for the run.
        - profile:
            The generated profile to be saved in the CSV file.

    """

    with open(filepath, "w") as f:
        profile.to_csv(
            f,  # type: ignore
            index=False,
            line_terminator="\n",
        )

    logger.info(
        "Profile data for year %s successfully saved to %s.", gen_year, filepath
    )


class BaseRenewablesNinjaThread(threading.Thread):
    """
    A :class:`threading.Thread` child for running data fetching in the background.

    .. attribute:: auto_generated_files_directory
        The directory in which CLOVER-generated files should be saved.

    .. attribute:: generation_inputs:
        The generation inputs information, extracted from the generation-inputs file.

    .. attribute:: location
        The location currently being considered.

    .. attribute:: logger
        The :class:`logging.Logger` to use for the run.

    .. attribute:: profile_prefix
        A prefix to append to the filenames.

    .. attribute:: regenerate
        Whether the profiles are to be regenerated, i.e., re-fetched from the
        renewables.ninja API (True) or whether existing profiles should be used if
        present (False).

    """

    profile_name: str
    profile_key: str

    def __init__(
        self,
        auto_generated_files_directory: str,
        generation_inputs: Dict[str, Any],
        location: Location,
        logger_name: str,
        regenerate: bool,
        sleep_multiplier: int,
        verbose: bool,
        *,
        renewables_ninja_params: Dict[str, Any],
        profile_prefix: str = "",
    ) -> None:
        """
        Instantiate a renewables-ninja-base-data thread.

        Inputs:
            - auto_generated_files_directory:
                The directory in which CLOVER-generated files should be saved.
            - generation_inputs:
                The generation inputs.
            - location:
                The location currently being considerted.
            - logger_name:
                The name to use for the logger.
            - profile_prefix:
                A prefix to append to the output filenames.
            - regenerate:
                Whether to regenerate the profiles.
            - renewables_ninja_params:
                Additional parameters to use when calling the renewables.ninja API.
            - sleep_multiplier:
                The multiplier to use when computing how long to sleep for, used when
                multiple threads are executed in parallel.
            - verbose:
                Whether to carry out verbose logging (True) or standard logging (False).

        """

        self.auto_generated_files_directory: str = auto_generated_files_directory
        self.generation_inputs: Dict[
            str, Union[bool, int, str, float]
        ] = generation_inputs
        self.location: Location = location
        self.logger: Logger = get_logger(logger_name, verbose)
        self.logger_name: str = logger_name
        self.profile_prefix: str = profile_prefix
        self.regenerate: bool = regenerate
        self.renewables_ninja_params: Dict[str, Any] = renewables_ninja_params
        self.sleep_multiplier: int = sleep_multiplier

        super().__init__()

    def __init_subclass__(cls, profile_name: str, profile_key: str) -> None:
        """
        Method run when instantiating a :class:`BaseRenewablesNinjaThread` child.

        Inputs:
            - profile_name:
                The name of the profile that is being generated.

        """

        super().__init_subclass__()
        cls.profile_name = profile_name
        cls.profile_key = profile_key

    def run(
        self,
    ) -> None:
        """
        Execute a renewables-ninja data-fetching thread.

        """

        if self.profile_name is None:
            self.logger.error(
                "%sRenewables Ninja base thread executed directly: execute child "
                "threads instead.%s",
                BColours.fail,
                BColours.endc,
            )
            raise InternalError(
                "Renewables Ninja threads cannot be called directly, only child "
                "threads can be executed."
            )

        self.logger.info(
            "RenewablesNinja data thread instantiated for %s profiles.",
            self.profile_name,
        )

        # To avoid a high burst of calls, a random sleep up to the sleep time is made.
        time.sleep(
            random.randint(0, RENEWABLES_NINJA_SLEEP_TIME * self.sleep_multiplier)
        )

        # A counter is used to keep track of calls to renewables.ninja to prevent
        # overloading.
        try:
            for year in tqdm(
                range(
                    int(self.generation_inputs["start_year"]),
                    int(self.generation_inputs["end_year"]) + 1,
                ),
                desc=f"{self.profile_name} "
                f"{self.profile_prefix[:-1].replace('_', ' ')} profiles",
                unit="year",
            ):
                # If the data file for the year already exists, skip.
                filename = (
                    f"{self.profile_prefix}{self.profile_name}_generation_{year}.csv"
                )
                filepath = os.path.join(self.auto_generated_files_directory, filename)

                if os.path.isfile(filepath) and not self.regenerate:
                    self.logger.info(
                        "Data file for year %s already exists, skipping.", year
                    )
                    continue

                self.logger.info(
                    "Fetching %s data for year %s.",
                    self.profile_name,
                    year,
                )
                try:
                    data = _get_profile_output(
                        str(self.generation_inputs[TOKEN]),
                        self.location,
                        self.logger,
                        self.profile_key,
                        self.renewables_ninja_params,
                        year,
                    )
                except KeyError as e:  # pylint: disable=invalid-name
                    self.logger.error("Missing data from input files: %s", str(e))
                    raise

                self.logger.info(
                    "Renewables.ninja for %s data successfully fetched.",
                    self.profile_name,
                )

                # Compute the total irradiance if the data is solar data.
                if self.profile_name == "solar":
                    try:
                        data[SolarDataType.TOTAL_IRRADIANCE.value] = (
                            data[SolarDataType.DIRECT_IRRADIANCE.value]
                            + data[SolarDataType.DIFFUSE_IRRADIANCE.value]
                        )
                    except KeyError as e:
                        self.logger.error(
                            "Failed to compute total irradiance for solar data "
                            "profile: %s",
                            str(e),
                        )
                        raise

                self.logger.info("Saving Renewables.ninja profile.")
                _save_profile_output(
                    filepath,
                    year,
                    self.logger,
                    data,
                )

                # The system waits to prevent overloading the renewables.ninja API and being
                # locked out.
                if year != self.generation_inputs["end_year"]:
                    time.sleep(RENEWABLES_NINJA_SLEEP_TIME * self.sleep_multiplier)

        except Exception:
            self.logger.error(
                "Error occured in profile fetching. See %s for details.",
                f"{os.path.join('logs', self.logger_name)}.log",
            )
            raise


def total_profile_output(
    generation_directory: str,
    regenerate: bool,
    start_year: int = 2007,
    num_years: int = 20,
    *,
    profile_name: str,
    profile_prefix: str,
) -> pd.DataFrame:
    """
    Generates total output data by taking the input years and repeating them.

    Inputs:
        - generation_directory:
            The directory in which generated profiles are saved.
        - regenerate:
            Whether to regenerate the profiles.
        - start_year:
            The year for which to begin the simulation.
        - num_years:
            The number of year for which to run the simulation.
        - profile_name:
            The name to use for saving the profiles.

                Outputs:
        .csv file for twenty years of PV output data

    """

    output = pd.DataFrame([])

    total_output_filename = os.path.join(
        generation_directory,
        f"{profile_prefix}{profile_name}_generation_{num_years}_years.csv",
    )

    # If the total output file already exists then simply read this in.
    if os.path.isfile(total_output_filename) and not regenerate:
        with open(total_output_filename, "r") as f:
            total_output = pd.read_csv(f)

    else:
        # Get data for each year using iteration, and add that data to the output file
        for year_index in tqdm(
            np.arange(min(10, num_years)),
            desc=f"total {profile_name} profile",
            leave=True,
            unit="year",
        ):
            iteration_year = start_year + year_index
            with open(
                os.path.join(
                    generation_directory,
                    f"{profile_prefix}{profile_name}_generation_{iteration_year}.csv",
                ),
                "r",
            ) as f:
                iteration_year_data = pd.read_csv(
                    f,
                )
            output = pd.concat([output, iteration_year_data], ignore_index=True)

        # Repeat the initial data in consecutive periods
        total_output = pd.DataFrame([])
        for _ in range(int(math.ceil(num_years / 10))):
            total_output = pd.concat([total_output, output], ignore_index=True)
        with open(total_output_filename, "w") as f:
            total_output.to_csv(
                f,  # type: ignore
                index=False,
                line_terminator="\n",
            )

    return total_output
