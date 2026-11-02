from pydantic import BaseModel, Field, model_validator

class BatteryConfig(BaseModel):
    capacity_kwh: float = Field(gt=0)
    soc: float = Field(ge=0, le=100)
    soh: float = Field(default=100, gt=0, le=100)

    min_soc: float = Field(default=10, ge=0, le=100)
    max_soc: float = Field(default=90, ge=0, le=100)

    max_charge_kw: float = Field(gt=0)
    max_discharge_kw: float = Field(gt=0)

    charge_efficiency: float = Field(default=0.95, gt=0, le=1)
    discharge_efficiency: float = Field(default=0.95, gt=0, le=1)

    max_charge_c: float | None = Field(default=None, gt = 0)
    max_discharge_c: float | None = Field(default=None, gt = 0)

    @model_validator(mode="after")
    def validate_limits(self):
        if self.min_soc >= self.max_soc:
            raise ValueError("min_soc, max_soc değerinden küçük olmalı")

        if not self.min_soc <= self.soc <= self.max_soc:
            raise ValueError("SOC izin verilen sınırlar içinde olmalı")

        return self


def calculate_effective_capacity(battery: BatteryConfig) -> float:
    return battery.capacity_kwh * battery.soh / 100


def calculate_usable_capacity(battery: BatteryConfig) -> float:
    effective_capacity = calculate_effective_capacity(battery)

    usable_energy = effective_capacity * (
        battery.max_soc - battery.min_soc
    ) / 100

    return round(usable_energy, 3)


def calculate_stored_energy(battery: BatteryConfig) -> float:
    effective_capacity = calculate_effective_capacity(battery)

    return round(
        effective_capacity * battery.soc / 100,
        3
    )


def calculate_effective_capacity(battery: BatteryConfig) -> float:
    return battery.capacity_kwh * battery.soh / 100


def calculate_usable_capacity(battery: BatteryConfig) -> float:
    effective_capacity = calculate_effective_capacity(battery)

    usable_energy = effective_capacity * (
        battery.max_soc - battery.min_soc
    ) / 100

    return round(usable_energy, 3)


def calculate_stored_energy(battery: BatteryConfig) -> float:
    effective_capacity = calculate_effective_capacity(battery)

    return round(
        effective_capacity * battery.soc / 100,
        3
    )

def calculate_charge(
    battery: BatteryConfig,
    requested_power_kw: float,
    duration_hours: float = 1.0
) -> dict:

    if requested_power_kw < 0 or duration_hours <= 0:
        raise ValueError("Güç negatif, süre sıfır veya negatif olamaz")

    effective_capacity = calculate_effective_capacity(battery)

    max_storable_energy = effective_capacity * (
        battery.max_soc - battery.soc
    ) / 100

    actual_power_kw = min(
        requested_power_kw,
        battery.max_charge_kw,
        max_storable_energy / (
            battery.charge_efficiency * duration_hours
        )
    )

    input_energy_kwh = actual_power_kw * duration_hours

    stored_energy_kwh = (
        input_energy_kwh * battery.charge_efficiency
    )

    new_soc = battery.soc + (
        stored_energy_kwh / effective_capacity
    ) * 100

    return {
        "actual_power_kw": round(actual_power_kw, 3),
        "input_energy_kwh": round(input_energy_kwh, 3),
        "stored_energy_kwh": round(stored_energy_kwh, 3),
        "new_soc": round(min(new_soc, battery.max_soc), 3)
    }

def calculate_discharge(
    battery: BatteryConfig,
    requested_power_kw: float,
    duration_hours: float = 1.0
) -> dict:

    if requested_power_kw < 0 or duration_hours <= 0:
        raise ValueError("Güç negatif, süre sıfır veya negatif olamaz")

    effective_capacity = calculate_effective_capacity(battery)

    available_energy = effective_capacity * (
        battery.soc - battery.min_soc
    ) / 100

    actual_power_kw = min(
        requested_power_kw,
        battery.max_discharge_kw,
        available_energy * battery.discharge_efficiency / duration_hours
    )

    output_energy_kwh = actual_power_kw * duration_hours

    removed_energy_kwh = (
        output_energy_kwh / battery.discharge_efficiency
    )

    new_soc = battery.soc - (
        removed_energy_kwh / effective_capacity
    ) * 100

    return {
        "actual_power_kw": round(actual_power_kw, 3),
        "output_energy_kwh": round(output_energy_kwh, 3),
        "removed_energy_kwh": round(removed_energy_kwh, 3),
        "new_soc": round(max(new_soc, battery.min_soc), 3)
    }

def simulate_battery(
    battery: BatteryConfig,
    schedule: list
) -> list:

    current_battery = battery.model_copy(deep=True)
    results = []

    for step in schedule:
        action = step["action"]
        power_kw = step.get("power_kw", 0)
        duration_hours = step.get("duration_hours", 1.0)

        previous_soc = current_battery.soc

        if action == "charge":
            result = calculate_charge(
                current_battery,
                power_kw,
                duration_hours
            )

        elif action == "discharge":
            result = calculate_discharge(
                current_battery,
                power_kw,
                duration_hours
            )

        elif action == "idle":
            result = {
                "actual_power_kw": 0,
                "new_soc": current_battery.soc
            }

        else:
            raise ValueError(f"Geçersiz işlem: {action}")

        current_battery.soc = result["new_soc"]

        results.append({
            "action": action,
            "previous_soc": previous_soc,
            **result
        })

    return results