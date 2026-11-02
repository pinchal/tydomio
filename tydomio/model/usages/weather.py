"""Weather: fields of the `weather` usage."""

from pydantic import Field

from .base import Reading, TydomEnum, UsageModel


class WeatherCondition(TydomEnum):
    """Values of `weather`."""

    DAY_BROKEN_CLOUDS = "DAY_BROKEN_CLOUDS"
    DAY_CLEAR_SKY = "DAY_CLEAR_SKY"
    DAY_FEW_CLOUDS = "DAY_FEW_CLOUDS"
    DAY_MIST = "DAY_MIST"
    DAY_RAIN = "DAY_RAIN"
    DAY_SCATTERED_CLOUDS = "DAY_SCATTERED_CLOUDS"
    DAY_SHOWER_RAIN = "DAY_SHOWER_RAIN"
    DAY_SNOW = "DAY_SNOW"
    DAY_THUNDERSTORM = "DAY_THUNDERSTORM"
    NIGHT_BROKEN_CLOUDS = "NIGHT_BROKEN_CLOUDS"
    NIGHT_CLEAR_SKY = "NIGHT_CLEAR_SKY"
    NIGHT_FEW_CLOUDS = "NIGHT_FEW_CLOUDS"
    NIGHT_MIST = "NIGHT_MIST"
    NIGHT_RAIN = "NIGHT_RAIN"
    NIGHT_SCATTERED_CLOUDS = "NIGHT_SCATTERED_CLOUDS"
    NIGHT_SHOWER_RAIN = "NIGHT_SHOWER_RAIN"
    NIGHT_SNOW = "NIGHT_SNOW"
    NIGHT_THUNDERSTORM = "NIGHT_THUNDERSTORM"
    UNAVAILABLE = "UNAVAILABLE"


class Weather(UsageModel):
    """Weather."""

    current_power: Reading[float] | None = Field(
        default=None, alias="currentPower", json_schema_extra={"unit": "Wh/m2"}
    )
    daily_power: Reading[float] | None = Field(
        default=None, alias="dailyPower", json_schema_extra={"unit": "Wh/m2"}
    )
    max_daily_out_temp: Reading[float] | None = Field(
        default=None,
        alias="maxDailyOutTemp",
        description="Highest outdoor temperature of the day",
        json_schema_extra={"unit": "degC"},
    )
    out_temperature: Reading[float] | None = Field(
        default=None,
        alias="outTemperature",
        description="Outdoor temperature",
        json_schema_extra={"unit": "degC"},
    )
    weather: Reading[WeatherCondition | str] | None = Field(
        default=None, alias="weather"
    )
