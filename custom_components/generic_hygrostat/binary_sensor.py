"""
Adds support for generic hygrostat units.
"""
import asyncio
import collections
from datetime import timedelta, datetime
import logging

import voluptuous as vol

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    PLATFORM_SCHEMA
)
from homeassistant.const import (
    CONF_NAME,
    CONF_UNIQUE_ID,
    STATE_ON,
    STATE_OFF,
    STATE_UNKNOWN,
)
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

CONF_SENSOR = "sensor"
CONF_ATTRIBUTE = "attribute"
CONF_DELTA_TRIGGER = "delta_trigger"
CONF_TARGET_OFFSET = "target_offset"
CONF_MIN_ON_TIME = "min_on_time"
CONF_MAX_ON_TIME = "max_on_time"
CONF_MIN_HUMIDITY = "min_humidity"
CONF_SAMPLE_INTERVAL = "sample_interval"

DEFAULT_NAME = "Generic Hygrostat"
DEFAULT_DELTA_TRIGGER = 3
DEFAULT_TARGET_OFFSET = 3
DEFAULT_MIN_ON_TIME = timedelta(seconds=0)
DEFAULT_MAX_ON_TIME = timedelta(seconds=7200)
DEFAULT_SAMPLE_INTERVAL = timedelta(minutes=5)
DEFAULT_MIN_HUMIDITY = 0

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_SENSOR): cv.entity_id,
        vol.Optional(CONF_ATTRIBUTE): cv.string,
        vol.Optional(CONF_DELTA_TRIGGER, default=DEFAULT_DELTA_TRIGGER): vol.Coerce(float),
        vol.Optional(CONF_TARGET_OFFSET, default=DEFAULT_TARGET_OFFSET): vol.Coerce(float),
        vol.Optional(CONF_MIN_ON_TIME, default=DEFAULT_MIN_ON_TIME): cv.time_period,
        vol.Optional(CONF_MAX_ON_TIME, default=DEFAULT_MAX_ON_TIME): cv.time_period,
        vol.Optional(CONF_SAMPLE_INTERVAL, default=DEFAULT_SAMPLE_INTERVAL): cv.time_period,
        vol.Optional(CONF_MIN_HUMIDITY, default=DEFAULT_MIN_HUMIDITY): vol.Coerce(float),
        vol.Optional(CONF_UNIQUE_ID): cv.string,
    }
)

ATTR_NUMBER_OF_SAMPLES = "number_of_samples"
ATTR_LOWEST_SAMPLE = "lowest_sample"
ATTR_TARGET = "target"
ATTR_MIN_ON_TIMER = "min_on_timer"
ATTR_MAX_ON_TIMER = "max_on_timer"
ATTR_MIN_HUMIDITY = "min_humidity"

SAMPLE_DURATION = timedelta(minutes=15)

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Generic Hygrostat platform."""
    _LOGGER.debug("Starting setup of generic_hygrostat")
    name = config[CONF_NAME]
    sensor_id = config[CONF_SENSOR]
    
    # Wacht maximaal 60 seconden op de sensor
    for i in range(60):
        _LOGGER.debug("Attempt %d: Looking for sensor %s", i + 1, sensor_id)
        sensor = hass.states.get(sensor_id)
        if sensor is not None:
            _LOGGER.info("Found sensor %s after %d seconds with state: %s", 
                        sensor_id, i, sensor.state)
            break
        await asyncio.sleep(1)
    
    if sensor is None:
        _LOGGER.error("Failed to find sensor %s after 60 seconds. Available entities: %s", 
                     sensor_id, 
                     [e.entity_id for e in hass.states.async_all() if e.entity_id.startswith('sensor.')])
        return False

    async_add_entities([
        GenericHygrostat(
            hass,
            config[CONF_NAME],
            config[CONF_SENSOR],
            config.get(CONF_ATTRIBUTE),
            config[CONF_DELTA_TRIGGER],
            config[CONF_TARGET_OFFSET],
            config[CONF_MIN_ON_TIME],
            config[CONF_MAX_ON_TIME],
            config[CONF_SAMPLE_INTERVAL],
            config[CONF_MIN_HUMIDITY],
            config.get(CONF_UNIQUE_ID),
        )
    ])
    return True


class GenericHygrostat(BinarySensorEntity):
    """Representation of a Generic Hygrostat device."""

    def __init__(
        self,
        hass,
        name,
        sensor_id,
        sensor_attribute,
        delta_trigger,
        target_offset,
        min_on_time,
        max_on_time,
        sample_interval,
        min_humidity,
        unique_id,
    ):
        """Initialize the hygrostat."""
        self.hass = hass
        self._name = name
        self.sensor_id = sensor_id
        self.sensor_attribute = sensor_attribute
        self.delta_trigger = delta_trigger
        self.target_offset = target_offset
        self.min_on_time = min_on_time
        self.max_on_time = max_on_time
        self.min_humidity = min_humidity
        self._unique_id = unique_id
        self._sample_interval = sample_interval

        self.sensor_humidity = None
        self.target = None
        sample_size = int(SAMPLE_DURATION / sample_interval)
        self.samples = collections.deque([], sample_size)
        self.min_on_timer = None
        self.max_on_timer = None

        self._state = STATE_OFF
        self._attr_device_class = BinarySensorDeviceClass.MOISTURE
        self._icon = "mdi:water-percent"

        _LOGGER.debug(
            "Initialized hygrostat %s with sensor %s, sample interval %s", 
            name, sensor_id, sample_interval
        )
        
        self._remove_update_interval = None

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        
        # Voer direct een eerste update uit
        self.update_humidity()
        
        # Start de periodieke updates
        self._remove_update_interval = async_track_time_interval(
            self.hass, self._async_update_ha, self._sample_interval
        )

    async def async_will_remove_from_hass(self):
        """Run when entity will be removed."""
        if self._remove_update_interval is not None:
            self._remove_update_interval()

    @callback
    async def _async_update_ha(self, now=None):
        """Update the entity."""
        _LOGGER.debug("%s: Running scheduled update", self.name)
        self._async_update(now)
        self.async_write_ha_state()

    @callback
    def _async_update(self, now=None):
        """Update the entity."""
        _LOGGER.debug("Running update for %s", self.name)
        self.update_humidity()
        
        if self.sensor_humidity is None:
            _LOGGER.debug("No humidity value available")
            return

        if self.min_on_timer and self.min_on_timer > datetime.now():
            _LOGGER.debug("Min on timer still active for %s", self.name)
            return

        if self.target and self.sensor_humidity <= self.target:
            _LOGGER.debug("Target reached for %s", self.name)
            self.set_off()
            return

        if self.max_on_timer and self.max_on_timer < datetime.now():
            _LOGGER.debug("Max on timer reached for %s", self.name)
            self.set_off()
            return

        if self.sensor_humidity < self.min_humidity:
            _LOGGER.debug("Below min humidity for %s", self.name)
            self.set_off()
            return

        delta = self.calc_delta()
        if delta is not None:
            _LOGGER.debug(
                "%s: Current delta: %s (trigger: %s)", 
                self.name, delta, self.delta_trigger
            )
            if delta >= self.delta_trigger:
                _LOGGER.debug("Delta trigger reached for %s: %s", self.name, delta)
                self.set_on()

    def update_humidity(self):
        """Update local humidity state from source sensor."""
        sensor = self.hass.states.get(self.sensor_id)
        
        _LOGGER.debug(
            "Updating humidity for %s from sensor %s (current state: %s)", 
            self.name, 
            self.sensor_id,
            sensor.state if sensor else "None"
        )

        if sensor is None:
            _LOGGER.warning("Unable to find sensor %s", self.sensor_id)
            return

        try:
            value = float(sensor.state)
            _LOGGER.debug(
                "%s: Got humidity value: %s", 
                self.name,
                value
            )
            self.sensor_humidity = value
            self.add_sample(value)
        except (ValueError, KeyError) as ex:
            _LOGGER.error("Unable to update from sensor: %s", ex)
            return

    def add_sample(self, value):
        """Add humidity sample to shift register."""
        if value is not None:
            self.samples.append(value)
            _LOGGER.debug(
                "%s: Sample added. Total samples: %d, Values: %s", 
                self.name,
                len(self.samples),
                list(self.samples)
            )

    def get_lowest_sample(self):
        """Get lowest humidity sample."""
        try:
            return min(self.samples)
        except ValueError:
            return None

    def calc_delta(self):
        """Calculate humidity delta."""
        lowest = self.get_lowest_sample()
        if lowest is None or self.sensor_humidity is None:
            return None
        return self.sensor_humidity - lowest

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        return self._icon

    @property
    def is_on(self):
        """Return true if the binary sensor is on."""
        return self._state == STATE_ON

    @property
    def state(self):
        """Return the state of the binary sensor."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {
            ATTR_NUMBER_OF_SAMPLES: len(self.samples),
            ATTR_LOWEST_SAMPLE: self.get_lowest_sample(),
            ATTR_TARGET: self.target,
            ATTR_MIN_ON_TIMER: self.min_on_timer,
            ATTR_MAX_ON_TIMER: self.max_on_timer,
            ATTR_MIN_HUMIDITY: self.min_humidity,
        }

    @property
    def unique_id(self):
        """Return the unique id of this hygrostat."""
        return self._unique_id

    def set_state(self, state):
        """Set the state of the sensor."""
        if self._state != state:
            self._state = state
            _LOGGER.debug("%s: State changed to %s", self.name, state)

    def set_on(self):
        """Turn the hygrostat on."""
        if self._state != STATE_ON:
            _LOGGER.info("%s: Turning on", self.name)
            self.set_state(STATE_ON)
            self.target = self.get_lowest_sample() + self.target_offset if self.get_lowest_sample() else None
            self.min_on_timer = datetime.now() + self.min_on_time if self.min_on_time else None
            self.max_on_timer = datetime.now() + self.max_on_time if self.max_on_time else None
            _LOGGER.debug(
                "%s: Set target: %s, min_timer: %s, max_timer: %s",
                self.name, self.target, self.min_on_timer, self.max_on_timer
            )

    def set_off(self):
        """Turn the hygrostat off."""
        if self._state != STATE_OFF:
            _LOGGER.info("%s: Turning off", self.name)
            self.set_state(STATE_OFF)
            self.target = None
            self.min_on_timer = None
            self.max_on_timer = None
