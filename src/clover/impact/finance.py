#!/usr/bin/python3
########################################################################################
# finance.py - Financial impact assessment module.                                     #
#                                                                                      #
# Author: Phil Sandwell, Ben Winchester                                                #
# Copyright: Phil Sandwell, 2021                                                       #
# License: Open source                                                                 #
# Most recent update: 05/08/2021                                                       #
#                                                                                      #
# For more information, please email:                                                  #
#     philip.sandwell@gmail.com                                                        #
########################################################################################
"""
finance.py - The finance module for CLOVER.

When assessing the impact of a system, the financial impact, i.e., the costs, need to be
considered. This module assesses the costs of a system based on the financial
information and system-sizing information provided.

"""

from logging import Logger
from typing import Any, Dict, List

import numpy as np  # type: ignore  # pylint: disable=import-error
import pandas as pd  # type: ignore  # pylint: disable=import-error

from .__utils__ import ImpactingComponent, LIFETIME, SIZE_INCREMENT
from ..__utils__ import (
    BColours,
    InputFileError,
    Location,
    hourly_profile_to_daily_sum,
)

__all_ = (
    "connections_expenditure",
    "COSTS",
    "diesel_fuel_expenditure",
    "discounted_energy_total",
    "discounted_equipment_cost",
    "expenditure",
    "get_total_equipment_cost",
    "ImpactingComponent",
    "independent_expenditure",
    "total_om",
)

# Connection cost:
#   Keyword used to denote the connection cost for a household within the community.
CONNECTION_COST = "connection_cost"

# Cost:
#   Keyword used to denote the cost of a component.
COST: str = "cost"

# Costs:
#   Keyword used for parsing device-specific cost information.
COSTS: str = "costs"

# Cost decrease:
#   Keyword used to denote the cost decrease of a component.
COST_DECREASE: str = "cost_decrease"

# Discount rate:
#   Keyword used to denote the discount rate.
DISCOUNT_RATE = "discount_rate"

# General OM:
#   Keyword used to denote general O&M costs of the system.
GENERAL_OM = "general_o&m"

# Installation cost:
#   Keyword used to denote the installation cost of a component.
INSTALLATION_COST: str = "cost"

# Installation cost decrease:
#   Keyword used to denote the installation cost decrease of a component.
INSTALLATION_COST_DECREASE: str = "cost_decrease"

# OM:
#   Keyword used to denote O&M costs.
OM = "o&m"


####################
# Helper functions #
####################


def _component_cost(
    component_cost: float,
    component_cost_decrease: float,
    component_size: float,
    installation_year=0,
) -> float:
    """
    Computes and returns the cost the system componenet based on the parameters.

    The various system component costs are comnputed using the following formula:
        size * cost * (1 - 0.01 * cost_decrease) ** installation_year

    Inputs:
        - component_cost:
            The cost of the component being considered.
        - component_cost_decrease:
            The cost decrease of the component being considered.
        - component_size:
            The size of the component within the minigrid system.
        - installation_year:
            The year that the component was installed.

    Outputs:
        - The undiscounted cost of the component.

    """

    system_wide_cost = component_cost * component_size
    annual_reduction = 0.01 * component_cost_decrease
    return system_wide_cost * (1 - annual_reduction) ** installation_year


def _component_installation_cost(
    component_size: float,
    installation_cost: float,
    installation_cost_decrease: float,
    installation_year: int = 0,
) -> float:
    """
    Calculates cost of system installation.

    The formula used is:
        installation_cost = (
            component_size * installation_cost * (
                1 - 0.01 * installation_cost_decrease
            ) ** installation_year
        )

    Inputs:
        - component_size:
            The size of the component within the minigrid system.
        - installation_cost:
            The cost of the installation.
        - installation_cost_decrease:
            The decrease in the cost of the installation.
        - installation_year:
            The installation year.

    Outputs:
        The undiscounted installation cost.

    """

    total_component_installation_cost = component_size * installation_cost
    annual_reduction = 0.01 * installation_cost_decrease

    return (
        total_component_installation_cost
        * (1.0 - annual_reduction) ** installation_year
    )


def _component_om(
    component_om_cost: float,
    component_size: float,
    finance_inputs: Dict[str, Any],
    logger: Logger,
    *,
    start_year: int,
    end_year: int
) -> float:
    """
    Computes the O&M cost of a component.

    """

    om_cost_daily = (component_size * component_om_cost) / 365
    total_daily_cost = pd.DataFrame([om_cost_daily] * (end_year - start_year) * 265)

    return discounted_energy_total(
        finance_inputs,
        logger,
        total_daily_cost,
        start_year=start_year,
        end_year=end_year,
    )


def _daily_discount_rate(discount_rate: float) -> float:
    """
    Calculates equivalent discount rate at a daily resolution

    Inputs:
        - discount_rate:
            The discount rate.

    Outputs:
        - The daily discount rate.

    """

    return ((1.0 + discount_rate) ** (1.0 / 365.0)) - 1.0


def _discounted_fraction(
    discount_rate: float, *, start_year: int = 0, end_year: int = 20
) -> pd.DataFrame:
    """
    Calculates the discounted fraction at a daily resolution

    Inputs:
        - discount_rate:
            The discount rate.
        - start_year:
            Start year of simulation period
        - end_year:
            End year of simulation period

    Outputs:
        Discounted fraction for each day of the simulation as a
        :class:`pandas.DataFrame` instance.

    """

    # Intialise various variables.
    start_day = int(start_year * 365)
    end_day = int(end_year * 365)

    # Convert the discount rate into the denominator.
    r_d = _daily_discount_rate(discount_rate)
    denominator = 1.0 + r_d

    # Compute a list containing all the discounted fractions over the time period.
    discounted_fraction_array = [
        denominator ** -time for time in range(start_day, end_day)
    ]

    return pd.DataFrame(discounted_fraction_array)


def _inverter_expenditure(
    finance_inputs: Dict[str, Any],
    location: Location,
    yearly_load_statistics: pd.DataFrame,
    *,
    start_year: int,
    end_year: int
) -> float:
    """
    Calculates cost of inverters based on load calculations

    Inputs:
        - finance_inputs:
            The finance-input information for the system.
        - location:
            The location being considered.
        - yearly_load_statistics:
            The yearly-load statistics for the system.
        - start_year:
            Start year of simulation period
        - end_year:
            End year of simulation period

    Outputs:
        Discounted cost

    """

    # Initialise inverter replacement periods
    replacement_period = finance_inputs[ImpactingComponent.INVERTER.value][LIFETIME]
    replacement_intervals = pd.DataFrame(
        np.arange(0, location.max_years, replacement_period)
    )
    replacement_intervals.columns = pd.Index(["Installation year"])

    # Check if inverter should be replaced in the specified time interval
    if replacement_intervals.loc[
        replacement_intervals["Installation year"].isin(
            list(range(start_year, end_year))
        )
    ].empty:
        inverter_discounted_cost = float(0.0)
        return inverter_discounted_cost

    # Initialise inverter sizing calculation
    max_power = []
    inverter_step = finance_inputs[ImpactingComponent.INVERTER.value][SIZE_INCREMENT]
    inverter_size: List[float] = []
    for i in range(len(replacement_intervals)):
        # Calculate maximum power in interval years
        start = replacement_intervals["Installation year"].iloc[i]
        end = start + replacement_period
        max_power_interval = yearly_load_statistics["Maximum"].iloc[start:end].max()
        max_power.append(max_power_interval)
        # Calculate resulting inverter size
        inverter_size_interval: float = (
            np.ceil(0.001 * max_power_interval / inverter_step) * inverter_step
        )
        inverter_size.append(inverter_size_interval)
    inverter_size_data_frame: pd.DataFrame = pd.DataFrame(inverter_size)
    inverter_size_data_frame.columns = pd.Index(["Inverter size (kW)"])
    inverter_info = pd.concat([replacement_intervals, inverter_size_data_frame], axis=1)
    # Calculate
    inverter_info["Discount rate"] = [
        (1 - finance_inputs[DISCOUNT_RATE])
        ** inverter_info["Installation year"].iloc[i]
        for i in range(len(inverter_info))
    ]
    inverter_info["Inverter cost ($/kW)"] = [
        finance_inputs[ImpactingComponent.INVERTER.value][COST]
        * (1 - 0.01 * finance_inputs[ImpactingComponent.INVERTER.value][COST_DECREASE])
        ** inverter_info["Installation year"].iloc[i]
        for i in range(len(inverter_info))
    ]
    inverter_info["Discounted expenditure ($)"] = [
        inverter_info["Discount rate"].iloc[i]
        * inverter_info["Inverter size (kW)"].iloc[i]
        * inverter_info["Inverter cost ($/kW)"].iloc[i]
        for i in range(len(inverter_info))
    ]
    inverter_discounted_cost = np.sum(
        inverter_info.loc[  # type: ignore
            inverter_info["Installation year"].isin(
                list(np.array(range(start_year, end_year)))
            )
        ]["Discounted expenditure ($)"]
    ).round(2)
    return inverter_discounted_cost


def _misc_costs(diesel_size: float, misc_costs: float, pv_array_size: float) -> float:
    """
    Calculates cost of miscellaneous capacity-related costs

    Inputs:
        - diesel_size:
            Capacity of diesel generator being installed
        - misc_costs:
            The misc. costs of the system.
        - pv_array_size:
            Capacity of PV being installed

    Outputs:
        The undiscounted cost.

    """

    misc_costs = (pv_array_size + diesel_size) * misc_costs
    return misc_costs


###############################
# Externally facing functions #
###############################


def get_total_equipment_cost(
    clean_water_tanks: float,
    diesel_size: float,
    finance_inputs: Dict[str, Any],
    hot_water_tanks: float,
    logger: Logger,
    pv_array_size: float,
    pvt_array_size: float,
    storage_size: float,
    installation_year: int = 0,
) -> float:
    """
    Calculates all equipment costs.

    Inputs:
        - clean_water_tanks:
            The number of clean-water tanks being installed.
        - diesel_size:
            Capacity of diesel generator being installed
        - finance_inputs:
            The finance-input information, parsed from the finance-inputs file.
        - hot_water_tanks:
            The number of hot-water tanks being installed.
        - logger:
            The logger to use for the run.
        - pv_array_size:
            Capacity of PV being installed
        - pvt_array_size:
            Capacity of PV-T being installed
        - storage_size:
            Capacity of battery storage being installed
        - installation_year:
            Installation year

    Outputs:
        The combined undiscounted cost of the system equipment.
    """

    # Calculate the various system costs.
    bos_cost = _component_cost(
        finance_inputs[ImpactingComponent.BOS.value][COST],
        finance_inputs[ImpactingComponent.BOS.value][COST_DECREASE],
        pv_array_size,
        installation_year,
    )

    if (
        ImpactingComponent.CLEAN_WATER_TANK.value not in finance_inputs
        and clean_water_tanks > 0
    ):
        logger.error(
            "%sNo clean-water tank financial input information provided.%s",
            BColours.fail,
            BColours.endc,
        )
        raise InputFileError(
            "finance inputs",
            "No clean-water financial input information provided and a non-zero "
            "number of clean-water tanks are being considered.",
        )
    clean_water_tank_cost: float = 0
    clean_water_tank_installation_cost: float = 0
    if clean_water_tanks > 0:
        clean_water_tank_cost = _component_cost(
            finance_inputs[ImpactingComponent.CLEAN_WATER_TANK.value][COST],
            finance_inputs[ImpactingComponent.CLEAN_WATER_TANK.value][COST_DECREASE],
            clean_water_tanks,
            installation_year,
        )
        clean_water_tank_installation_cost = _component_installation_cost(
            clean_water_tanks,
            finance_inputs[ImpactingComponent.CLEAN_WATER_TANK.value][
                INSTALLATION_COST
            ],
            finance_inputs[ImpactingComponent.CLEAN_WATER_TANK.value][
                INSTALLATION_COST_DECREASE
            ],
            installation_year,
        )

    diesel_cost = _component_cost(
        finance_inputs[ImpactingComponent.DIESEL.value][COST],
        finance_inputs[ImpactingComponent.DIESEL.value][COST_DECREASE],
        diesel_size,
        installation_year,
    )
    diesel_installation_cost = _component_installation_cost(
        pv_array_size,
        finance_inputs[ImpactingComponent.DIESEL.value][INSTALLATION_COST],
        finance_inputs[ImpactingComponent.DIESEL.value][INSTALLATION_COST_DECREASE],
        installation_year,
    )

    if (
        ImpactingComponent.HOT_WATER_TANK.value not in finance_inputs
        and hot_water_tanks > 0
    ):
        logger.error(
            "%sNo hot-water tank financial input information provided.%s",
            BColours.fail,
            BColours.endc,
        )
        raise InputFileError(
            "tank inputs",
            "No hot-water financial input information provided and a non-zero "
            "number of clean-water tanks are being considered.",
        )
    hot_water_tank_cost: float = 0
    hot_water_tank_installation_cost: float = 0
    if hot_water_tanks > 0:
        hot_water_tank_cost = _component_cost(
            finance_inputs[ImpactingComponent.HOT_WATER_TANK.value][COST],
            finance_inputs[ImpactingComponent.HOT_WATER_TANK.value][COST_DECREASE],
            hot_water_tanks,
            installation_year,
        )
        hot_water_tank_installation_cost = _component_installation_cost(
            hot_water_tanks,
            finance_inputs[ImpactingComponent.HOT_WATER_TANK.value][INSTALLATION_COST],
            finance_inputs[ImpactingComponent.HOT_WATER_TANK.value][
                INSTALLATION_COST_DECREASE
            ],
            installation_year,
        )

    pv_cost = _component_cost(
        finance_inputs[ImpactingComponent.PV.value][COST],
        finance_inputs[ImpactingComponent.PV.value][COST_DECREASE],
        pv_array_size,
        installation_year,
    )
    pv_installation_cost = _component_installation_cost(
        pv_array_size,
        finance_inputs[ImpactingComponent.PV.value][INSTALLATION_COST],
        finance_inputs[ImpactingComponent.PV.value][INSTALLATION_COST_DECREASE],
        installation_year,
    )

    if ImpactingComponent.PV_T.value not in finance_inputs and pvt_array_size > 0:
        logger.error(
            "%sNo PV-T financial input information provided.%s",
            BColours.fail,
            BColours.endc,
        )
        raise InputFileError(
            "finance inputs",
            "No PV-T financial input information provided and a non-zero number of PV-T"
            "panels are being considered.",
        )
    pvt_cost: float = 0
    pvt_installation_cost: float = 0
    if pvt_array_size > 0:
        pvt_cost = _component_cost(
            finance_inputs[ImpactingComponent.PV_T.value][COST],
            finance_inputs[ImpactingComponent.PV_T.value][COST_DECREASE],
            pv_array_size,
            installation_year,
        )
        pvt_installation_cost = _component_installation_cost(
            pvt_array_size,
            finance_inputs[ImpactingComponent.PV_T.value][INSTALLATION_COST],
            finance_inputs[ImpactingComponent.PV_T.value][INSTALLATION_COST_DECREASE],
            installation_year,
        )

    storage_cost = _component_cost(
        finance_inputs[ImpactingComponent.STORAGE.value][COST],
        finance_inputs[ImpactingComponent.STORAGE.value][COST_DECREASE],
        storage_size,
        installation_year,
    )

    total_installation_cost = (
        clean_water_tank_installation_cost
        + diesel_installation_cost
        + hot_water_tank_installation_cost
        + pv_installation_cost
        + pvt_installation_cost
    )

    misc_costs = _misc_costs(
        diesel_size, finance_inputs[ImpactingComponent.MISC.value][COST], pv_array_size
    )
    return (
        bos_cost
        + clean_water_tank_cost
        + diesel_cost
        + hot_water_tank_cost
        + misc_costs
        + pv_cost
        + pvt_cost
        + storage_cost
        + total_installation_cost
    )


def connections_expenditure(
    finance_inputs: Dict[str, Any], households: pd.DataFrame, installation_year: int = 0
) -> float:
    """
    Calculates cost of connecting households to the system

    Inputs:
        - finance_inputs:
            The finance input information.
        - households:
            DataFrame of households from Energy_System().simulation(...)
        - year:
            Installation year

    Outputs:
        Discounted cost

    """

    new_connections = np.max(households) - np.min(households)
    undiscounted_cost = float(
        finance_inputs[ImpactingComponent.HOUSEHOLDS.value][CONNECTION_COST]
        * new_connections
    )
    discount_fraction: float = (
        1.0 - finance_inputs[DISCOUNT_RATE]
    ) ** installation_year
    total_discounted_cost = undiscounted_cost * discount_fraction

    # Section in comments allows a more accurate consideration of the discounted cost
    # for new connections, but substantially increases the processing time.

    # new_connections = [0]
    # for t in range(int(households.shape[0])-1):
    #     new_connections.append(households['Households'][t+1] - households['Households'][t])
    # new_connections = pd.DataFrame(new_connections)
    # new_connections_daily = hourly_profile_to_daily_sum(new_connections)
    # total_daily_cost = connection_cost * new_connections_daily
    # total_discounted_cost = self.discounted_cost_total(total_daily_cost,start_year,end_year)

    return total_discounted_cost


def diesel_fuel_expenditure(
    diesel_fuel_usage_hourly: pd.DataFrame,
    finance_inputs: Dict[str, Any],
    logger: Logger,
    *,
    start_year: int = 0,
    end_year: int = 20
) -> float:
    """
    Calculates cost of diesel fuel used by the system

    Inputs:
        - diesel_fuel_usage_hourly:
            Output from Energy_System().simulation(...)
        - finance_inputs:
            The finance input information.
        - logger:
            The logger to use for the run.
        - start_year:
            Start year of simulation period
        - end_year:
            End year of simulation period

    Outputs:
        Discounted cost

    """

    diesel_fuel_usage_daily = hourly_profile_to_daily_sum(diesel_fuel_usage_hourly)
    start_day = start_year * 365
    end_day = end_year * 365
    r_y = 0.01 * finance_inputs[ImpactingComponent.DIESEL_FUEL.value][COST_DECREASE]
    r_d = ((1.0 + r_y) ** (1.0 / 365.0)) - 1.0
    diesel_price_daily: pd.DataFrame = pd.DataFrame(
        [
            finance_inputs[ImpactingComponent.DIESEL_FUEL.value][COST]
            * (1.0 - r_d) ** day
            for day in range(start_day, end_day)
        ]
    )

    total_daily_cost = pd.DataFrame(
        diesel_fuel_usage_daily.values * diesel_price_daily.values
    )
    total_discounted_cost = discounted_energy_total(
        finance_inputs,
        logger,
        total_daily_cost,
        start_year=start_year,
        end_year=end_year,
    )

    return total_discounted_cost


def discounted_energy_total(
    finance_inputs: Dict[str, Any],
    logger: Logger,
    total_daily: pd.DataFrame,
    *,
    start_year: int = 0,
    end_year: int = 20
) -> float:
    """
    Calculates the total discounted cost of some parameter.

    Inputs:
        - finance_inputs:
            The finance input information.
        - logger:
            The logger to use for the run.
        - total_daily:
            Undiscounted energy at a daily resolution
        - start_year:
            Start year of simulation period
        - end_year:
            End year of simulation period

    Outputs:
        The discounted energy total cost.

    """

    try:
        discount_rate = finance_inputs[DISCOUNT_RATE]
    except KeyError:
        logger.error(
            "%sNo discount rate in the finance inputs, missing key: %s%s",
            BColours.fail,
            DISCOUNT_RATE,
            BColours.endc,
        )
        raise

    discounted_fraction = _discounted_fraction(
        discount_rate, start_year=start_year, end_year=end_year
    )
    discounted_energy = discounted_fraction * total_daily  # type: ignore
    return np.sum(discounted_energy)[0]  # type: ignore


def discounted_equipment_cost(
    clean_water_tanks: float,
    diesel_size: float,
    finance_inputs: Dict[str, Any],
    logger: Logger,
    pv_array_size: float,
    pvt_array_size: float,
    storage_size: float,
    installation_year=0,
) -> float:
    """
    Calculates cost of all equipment costs

    Inputs:
        - clean_water_tanks:
            The number of clean-water tanks being installed.
        - diesel_size:
            Capacity of diesel generator being installed
        - finance_inputs:
            The finance input information.
        - logger:
            The logger to use for the run.
        - pv_array_size:
            Capacity of PV being installed
        - pvt_array_size:
            Capacity of PV-T being installed
        - storage_size:
            Capacity of battery storage being installed
        - installation_year:
            Installation year
    Outputs:
        Discounted cost
    """

    undiscounted_cost = get_total_equipment_cost(
        clean_water_tanks,
        diesel_size,
        finance_inputs,
        logger,
        pv_array_size,
        pvt_array_size,
        storage_size,
        installation_year,
    )
    discount_fraction = (1.0 - finance_inputs[DISCOUNT_RATE]) ** installation_year

    return undiscounted_cost * discount_fraction


def expenditure(
    component: ImpactingComponent,
    finance_inputs,
    hourly_usage: pd.DataFrame,
    logger: Logger,
    *,
    start_year: int = 0,
    end_year: int = 20
):
    """
    Calculates cost of the usage of a component.

    Inputs:
        - component:
            The component to consider.
        - finance_inputs:
            The financial input information.
        - hourly_usage:
            Output from Energy_System().simulation(...)
        - start_year:
            Start year of simulation period
        - end_year:
            End year of simulation period

    Outputs:
        Discounted cost

    """

    hourly_cost = hourly_usage * finance_inputs[component.value][COST]
    total_daily_cost = hourly_profile_to_daily_sum(hourly_cost)
    total_discounted_cost = discounted_energy_total(
        finance_inputs,
        logger,
        total_daily_cost,
        start_year=start_year,
        end_year=end_year,
    )
    return total_discounted_cost


def independent_expenditure(
    finance_inputs: Dict[str, Any],
    location: Location,
    yearly_load_statistics: pd.DataFrame,
    *,
    start_year: int,
    end_year: int
):
    """
    Calculates cost of equipment which is independent of simulation periods

    Inputs:
        - finance_inputs:
            The financial input information.
        - location:
            The location currently being considered.
        - yearly_load_statistics:
            The yearly load statistics information.
        - start_year:
            Start year of simulation period
        - end_year:
            End year of simulation period

    Outputs:
        Discounted cost

    """

    inverter_expenditure = _inverter_expenditure(
        finance_inputs,
        location,
        yearly_load_statistics,
        start_year=start_year,
        end_year=end_year,
    )
    total_expenditure = inverter_expenditure  # ... + other components as required
    return total_expenditure


def total_om(
    clean_water_tanks: float,
    diesel_size: float,
    finance_inputs: Dict[str, Any],
    hot_water_tanks: float,
    logger: Logger,
    pv_array_size: float,
    pvt_array_size: float,
    storage_size: float,
    *,
    start_year: int = 0,
    end_year: int = 20
):
    """
    Calculates total O&M cost over the simulation period

    Inputs:
        - clean_water_tanks:
            The number of clean-water tanks installed.
        - diesel_size:
            Capacity of diesel generator installed.
        - finance_inputs:
            Finance input information.
        - hot_water_tanks:
            The number of hot-water tanks installed.
        - logger:
            The logger to use for the run.
        - pv_array_size:
            Capacity of PV installed.
        - pvt_array_size:
            Capacity of PV-T installed.
        - storage_size:
            Capacity of battery storage installed.
        - start_year:
            Start year of simulation period.
        - end_year:
            End year of simulation period.

    Outputs:
        Discounted cost

    """

    if (
        ImpactingComponent.CLEAN_WATER_TANK.value not in finance_inputs
        and clean_water_tanks > 0
    ):
        logger.error(
            "%sNo clean-water-tank financial input information provided.%s",
            BColours.fail,
            BColours.endc,
        )
        raise InputFileError(
            "tank inputs",
            "No clean-water tank financial input information provided and a non-zero "
            "number of clean-water tanks are being considered.",
        )
    clean_water_tank_om: float = 0
    if clean_water_tanks > 0:
        clean_water_tank_om = _component_om(
            finance_inputs[ImpactingComponent.CLEAN_WATER_TANK.value][OM],
            clean_water_tanks,
            finance_inputs,
            logger,
            start_year=start_year,
            end_year=end_year,
        )

    diesel_om = _component_om(
        finance_inputs[ImpactingComponent.DIESEL.value][OM],
        diesel_size,
        finance_inputs,
        logger,
        start_year=start_year,
        end_year=end_year,
    )

    general_om = _component_om(
        finance_inputs[GENERAL_OM],
        1,
        finance_inputs,
        logger,
        start_year=start_year,
        end_year=end_year,
    )

    if (
        ImpactingComponent.HOT_WATER_TANK.value not in finance_inputs
        and hot_water_tanks > 0
    ):
        logger.error(
            "%sNo hot-water-tank financial input information provided.%s",
            BColours.fail,
            BColours.endc,
        )
        raise InputFileError(
            "tank inputs",
            "No hot-water tank financial input information provided and a non-zero "
            "number of clean-water tanks are being considered.",
        )
    hot_water_tank_om: float = 0
    if hot_water_tanks > 0:
        hot_water_tank_om = _component_om(
            finance_inputs[ImpactingComponent.HOT_WATER_TANK.value][OM],
            hot_water_tanks,
            finance_inputs,
            logger,
            start_year=start_year,
            end_year=end_year,
        )

    pv_om = _component_om(
        finance_inputs[ImpactingComponent.PV.value][OM],
        pv_array_size,
        finance_inputs,
        logger,
        start_year=start_year,
        end_year=end_year,
    )

    if ImpactingComponent.PV_T.value not in finance_inputs and pvt_array_size > 0:
        logger.error(
            "%sNo PV-T financial input information provided.%s",
            BColours.fail,
            BColours.endc,
        )
        raise InputFileError(
            "finance inputs",
            "No PV-T financial input information provided and a non-zero number of PV-T"
            "panels are being considered.",
        )
    pvt_om: float = 0
    if pvt_array_size > 0:
        pvt_om = _component_om(
            finance_inputs[ImpactingComponent.PV_T.value][OM],
            pvt_array_size,
            finance_inputs,
            logger,
            start_year=start_year,
            end_year=end_year,
        )

    storage_om = _component_om(
        finance_inputs[ImpactingComponent.STORAGE.value][OM],
        storage_size,
        finance_inputs,
        logger,
        start_year=start_year,
        end_year=end_year,
    )

    return (
        clean_water_tank_om
        + diesel_om
        + general_om
        + hot_water_tank_om
        + pv_om
        + pvt_om
        + storage_om
    )


# #%%
# # ==============================================================================
# #   EQUIPMENT EXPENDITURE (DISCOUNTED)
# #       Find system equipment capital expenditure (discounted) for new equipment
# # ==============================================================================


# #   Grid extension components
# def get_grid_extension_cost(self, grid_extension_distance, year):
#     """
#     Function:
#         Calculates cost of extending the grid network to a community
#     Inputs:
#         grid_extension_distance     Distance to the existing grid network
#         year                        Installation year
#     Outputs:
#         Discounted cost
#     """
#     grid_extension_cost = self.finance_inputs.loc["Grid extension cost"]  # per km
#     grid_infrastructure_cost = self.finance_inputs.loc["Grid infrastructure cost"]
#     discount_fraction = (1.0 - self.finance_inputs.loc["Discount rate"]) ** year
#     return (
#         grid_extension_distance * grid_extension_cost * discount_fraction
#         + grid_infrastructure_cost
#     )


# #%%
# # =============================================================================
# #   EQUIPMENT EXPENDITURE (DISCOUNTED) ON INDEPENDENT EXPENDITURE
# #       Find expenditure (discounted) on items independent of simulation periods
# # =============================================================================


# #%%
# # ==============================================================================
# #   EXPENDITURE (DISCOUNTED) ON RUNNING COSTS
# #       Find expenditure (discounted) incurred during the simulation period
# # ==============================================================================

# #%%
# # ==============================================================================
# #   FINANCING CALCULATIONS
# #       Functions to calculate discount rates and discounted expenditures
# # ==============================================================================


# #   Calculate LCUE using total discounted costs ($) and discounted energy (kWh)
# def get_LCUE(self, total_discounted_costs, total_discounted_energy):
#     """
#     Function:
#         Calculates the levelised cost of used electricity (LCUE)
#     Inputs:
#         total_discounted_costs        Discounted costs total
#         total_discounted_energy       Discounted energy total
#     Outputs:
#         Levelised cost of used electricity
#     """
#     return total_discounted_costs / total_discounted_energy
