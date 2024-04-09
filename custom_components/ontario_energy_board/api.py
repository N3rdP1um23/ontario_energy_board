"""Utility methods used by the Ontario Energy Board integration.

TECH-DEBT: This class is not being in use anymore. At the moment it's only being
used for unit tests. These tests should instead be against the sensor component itself.
"""

import aiohttp
import async_timeout
import logging
import xml.etree.ElementTree as ET
from datetime import date
from typing import Final

import holidays
from homeassistant.util.dt import as_local, now

from .const import (
    ENERGY_SECTORS,
    ELECTRICITY_RATES_URL,
    NATURAL_GAS_RATES_URL,
    STATE_MID_PEAK,
    STATE_NO_PEAK,
    STATE_NO_PEAK_RATE,
    STATE_OFF_PEAK,
    STATE_ON_PEAK,
    STATE_ULO_MID_PEAK,
    STATE_ULO_ON_PEAK,
    STATE_ULO_OFF_PEAK,
    STATE_ULO_OVERNIGHT,
    XML_KEY_OFF_PEAK_RATE,
    XML_KEY_MID_PEAK_RATE,
    XML_KEY_ON_PEAK_RATE,
    XML_KEY_ULO_OVERNIGHT_RATE,
    XML_KEY_ULO_OFF_PEAK_RATE,
    XML_KEY_ULO_MID_PEAK_RATE,
    XML_KEY_ULO_ON_PEAK_RATE,
)

_LOGGER: Final = logging.getLogger(__name__)


async def get_energy_companies() -> list[str]:
    """Generates a list of all energy companies available
    in the XML document including the available classes.
    """

    all_companies = []

    for sector in ENERGY_SECTORS:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                ELECTRICITY_RATES_URL
                if sector == "electricity"
                else NATURAL_GAS_RATES_URL
            ) as response:
                content = await response.text()

        tree = ET.fromstring(content)

        session._base_url

        for company in tree.findall(
            "BillDataRow" if sector == "electricity" else "GasBillData"
        ):
            all_companies.append(
                "{company_name} ({company_class}) [{company_sector}]".format(
                    company_name=company.find("Dist").text,
                    company_class=company.find(
                        "Class" if sector == "electricity" else "SA"
                    ).text,
                    company_sector=sector.replace("_", " ").title(),
                )
            )

    all_companies.sort()

    return all_companies


class OntarioEnergyBoard:
    """Class to communication with the Ontario Energy Rate services."""

    _timeout = 10
    off_peak_rate = None
    mid_peak_rate = None
    on_peak_rate = None
    energy_sector = None
    ulo_overnight_rate = None
    ulo_off_peak_rate = None
    ulo_mid_peak_rate = None
    ulo_on_peak_rate = None

    def __init__(self, energy_company, ulo_enabled, websession):
        self.energy_company = energy_company
        self.ulo_enabled = ulo_enabled
        self.websession = websession
        self.ontario_holidays = holidays.Canada(prov="ON", observed=True)

    async def get_rates(self):
        """Parses the official XML document extracting the rates for
        the selected energy company.
        """

        for sector in ENERGY_SECTORS:
            async with async_timeout.timeout(self._timeout):
                response = await self.websession.get(
                    ELECTRICITY_RATES_URL
                    if sector == "electricity"
                    else NATURAL_GAS_RATES_URL
                )
            content = await response.text()
            tree = ET.fromstring(content)

            for company in tree.findall(
                "BillDataRow" if sector == "electricity" else "GasBillData"
            ):
                current_company = (
                    "{company_name} ({company_class}) [{company_sector}]".format(
                        company_name=company.find("Dist").text,
                        company_class=company.find(
                            "Class" if sector == "electricity" else "SA"
                        ).text,
                        company_sector=sector.replace("_", " ").title(),
                    )
                )

                if current_company == self.energy_company:
                    self.off_peak_rate = (
                        float(company.find(XML_KEY_OFF_PEAK_RATE).text)
                        if sector == "electricity"
                        else STATE_NO_PEAK_RATE
                    )
                    self.mid_peak_rate = (
                        float(company.find(XML_KEY_MID_PEAK_RATE).text)
                        if sector == "electricity"
                        else STATE_NO_PEAK_RATE
                    )
                    self.on_peak_rate = (
                        float(company.find(XML_KEY_ON_PEAK_RATE).text)
                        if sector == "electricity"
                        else STATE_NO_PEAK_RATE
                    )
                    self.energy_sector = sector
                    self.ulo_overnight_rate = (
                        float(company.find(XML_KEY_ULO_OVERNIGHT_RATE).text)
                        if sector == "electricity"
                        else STATE_NO_PEAK_RATE
                    )
                    self.ulo_off_peak_rate = (
                        float(company.find(XML_KEY_ULO_OFF_PEAK_RATE).text)
                        if sector == "electricity"
                        else STATE_NO_PEAK_RATE
                    )
                    self.ulo_mid_peak_rate = (
                        float(company.find(XML_KEY_ULO_MID_PEAK_RATE).text)
                        if sector == "electricity"
                        else STATE_NO_PEAK_RATE
                    )
                    self.ulo_on_peak_rate = (
                        float(company.find(XML_KEY_ULO_ON_PEAK_RATE).text)
                        if sector == "electricity"
                        else STATE_NO_PEAK_RATE
                    )

            return

        _LOGGER.error("Could not find energy rates for %s", self.energy_company)

    @property
    def ulo_active_peak(self) -> str:
        """
        Find the active peak based on the current day and hour.

        According to OEB, ULO nighttime rates apply every day. On weekends and
        holidays, daytime is off-peak. On weekdays, late afternoon and early
        evening is on-peak. The rest is mid-peak.

        ULO prices and periods are the same all year round.
        """

        if self.energy_sector == "natural_gas":
            return STATE_NO_PEAK
        current_time = as_local(now())
        current_hour = int(current_time.strftime("%H"))

        is_overnight = current_hour < 7 or current_hour >= 23
        if is_overnight:
            return STATE_ULO_OVERNIGHT

        is_holiday = current_time.date() in self.ontario_holidays
        is_weekend = current_time.weekday() >= 5

        if is_holiday or is_weekend:
            return STATE_ULO_OFF_PEAK

        is_on_peak = 16 <= current_hour < 21
        if is_on_peak:
            return STATE_ULO_ON_PEAK

        return STATE_ULO_MID_PEAK

    @property
    def tou_active_peak(self):
        """
        Find the active peak based on the current day and hour.

        According to OEB, weekends and holidays are 24-hour off peak periods.
        During summer (observed from May 1st to Oct 31st), the morning and evening
        periods are mid-peak, and the afternoon is on-peak. This flips during winter
        time, where morning and evening are on-peak and afternoons mid-peak.
        """

        if self.energy_sector == "natural_gas":
            return STATE_NO_PEAK

        current_time = as_local(now())

        is_summer = (
            date(current_time.year, 5, 1)
            <= current_time.date()
            <= date(current_time.year, 10, 31)
        )
        is_holiday = current_time.date() in self.ontario_holidays
        is_weekend = current_time.weekday() >= 5

        if is_holiday or is_weekend:
            return STATE_OFF_PEAK

        current_hour = int(current_time.strftime("%H"))
        if (7 <= current_hour < 11) or (17 <= current_hour < 19):
            return STATE_MID_PEAK if is_summer else STATE_ON_PEAK
        if 11 <= current_hour < 17:
            return STATE_ON_PEAK if is_summer else STATE_MID_PEAK

        return STATE_OFF_PEAK

    @property
    def active_peak(self) -> str:
        if self.ulo_enabled:
            return self.ulo_active_peak
        else:
            return self.tou_active_peak

    @property
    def active_peak_rate(self):
        """Returns the current peak's rate."""
        return (
            getattr(self, f"{self.active_peak}_rate")
            if self.energy_sector == "electricity"
            else STATE_NO_PEAK
        )
