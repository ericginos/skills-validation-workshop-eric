"""Pydantic schemas defining structured outputs for Alaska Department of Snow (ADS).

Enforces schema compliance for document synthesis, including:
- PlowDispatch: Plow assignments, routes, mileposts, status
- RoadClosure: Highway closures, sections, reasons, reopening estimates
- RiskAssessment: Public safety risk levels (LOW, MEDIUM, HIGH, CRITICAL), hazards, mitigations
- WeatherMetrics: Snowfall rate, accumulation, wind speed, visibility
- EquipmentStatus: Plow/grader equipment readiness and status
- StructuredPayload: Combined structured data container
- DocumentSynthesisOutput: Top-level schema containing summary, payload, and risk assessment
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    """Enumeration of public safety risk levels."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ClosureType(str, Enum):
    """Enumeration of road closure types."""
    FULL_CLOSURE = "FULL_CLOSURE"
    LANE_RESTRICTION = "LANE_RESTRICTION"
    ADVISORY = "ADVISORY"
    CHAINS_REQUIRED = "CHAINS_REQUIRED"
    WEIGHT_RESTRICTION = "WEIGHT_RESTRICTION"


class WeatherMetrics(BaseModel):
    """Weather and storm severity metrics extracted from operational logs."""
    snowfall_rate_inches_per_hour: Optional[float] = Field(
        default=None, description="Observed or forecasted snowfall rate in inches per hour"
    )
    accumulated_snow_inches: Optional[float] = Field(
        default=None, description="Total snow accumulation in inches"
    )
    temperature_f: Optional[float] = Field(
        default=None, description="Current ambient temperature in Fahrenheit"
    )
    wind_speed_mph: Optional[float] = Field(
        default=None, description="Wind speed or gust velocity in miles per hour"
    )
    visibility_miles: Optional[float] = Field(
        default=None, description="Estimated visibility distance in miles"
    )
    storm_severity_rating: Optional[str] = Field(
        default=None, description="Qualitative storm assessment (e.g. Blizzard, Light Snow, Freezing Rain)"
    )


class EquipmentStatus(BaseModel):
    """Equipment status and tracking for winter maintenance gear."""
    equipment_id: str = Field(description="Unique equipment identifier, e.g. PLOW-104, BLOWER-02")
    equipment_type: str = Field(
        default="Snow Plow", description="Type of machinery (e.g. Plow Truck, Rotary Blower, Motor Grader)"
    )
    operational_status: str = Field(
        default="ACTIVE", description="Operational status: ACTIVE, STANDBY, MAINTENANCE_REQUIRED, OUT_OF_SERVICE"
    )
    fuel_level_percent: Optional[float] = Field(
        default=None, description="Fuel level percentage (0 - 100%)"
    )
    operator_id: Optional[str] = Field(
        default=None, description="Assigned operator/driver identifier"
    )


class PlowDispatch(BaseModel):
    """Plow and maintenance crew dispatch details."""
    dispatch_id: str = Field(description="Unique dispatch identifier, e.g. DISPATCH-AK-104 or D-01")
    route_name: str = Field(description="Highway, corridor, or route name (e.g. AK-1 Seward Highway, Glenn Hwy)")
    mileposts: Optional[str] = Field(
        default=None, description="Specific milepost range covered (e.g. MP 15 - MP 45)"
    )
    assigned_units: List[str] = Field(
        default_factory=list, description="List of plow units or vehicle IDs assigned to the route"
    )
    priority_level: str = Field(
        default="MEDIUM", description="Dispatch priority: PRIORITY_1, HIGH, MEDIUM, LOW"
    )
    status: str = Field(
        default="DISPATCHED", description="Dispatch status: DISPATCHED, EN_ROUTE, IN_PROGRESS, COMPLETED, SUSPENDED"
    )
    treatment_applied: Optional[str] = Field(
        default=None, description="Type of treatment applied (e.g. Plowing, Salt Brine, Sand/Gravel, Calcium Chloride)"
    )
    operator_notes: Optional[str] = Field(
        default=None, description="Special field notes or warnings from the driver/dispatcher"
    )


class RoadClosure(BaseModel):
    """Road, highway, or lane closure details."""
    highway_id: str = Field(description="Highway designation or name (e.g. AK-1, AK-3 Parks Hwy, Richardson Hwy)")
    affected_section: str = Field(
        description="Section description or milepost markers affected (e.g. MP 35 to MP 50 Turnagain Pass)"
    )
    closure_type: str = Field(
        default="FULL_CLOSURE", description="Type of restriction: FULL_CLOSURE, LANE_RESTRICTION, ADVISORY, CHAINS_REQUIRED"
    )
    reason: str = Field(
        description="Reason for closure (e.g. Active avalanche slide, Severe ice, Zero visibility blizzard, Stalled semi)"
    )
    detour_available: Optional[bool] = Field(
        default=None, description="Whether a viable detour route is available"
    )
    estimated_reopening: Optional[str] = Field(
        default=None, description="Estimated reopening date/time or condition (e.g. Tomorrow 08:00 AKST, Pending avalanche mitigation)"
    )


class RiskAssessment(BaseModel):
    """Public safety and operational risk assessment."""
    risk_level: str = Field(
        description="Overall risk level: LOW, MEDIUM, HIGH, or CRITICAL"
    )
    public_safety_score: Optional[float] = Field(
        default=None, description="Public safety hazard score on a scale of 1.0 (minimal) to 10.0 (extreme catastrophe)"
    )
    key_hazards: List[str] = Field(
        default_factory=list, description="Primary danger factors (e.g. Avalanche slide, Black ice, Whiteout, Stranded vehicles)"
    )
    mitigation_steps: List[str] = Field(
        default_factory=list, description="Actionable operational mitigation recommendations"
    )
    recommended_public_alert: Optional[str] = Field(
        default=None, description="Recommended text for public broadcast / 511 highway travel alert"
    )


class StructuredPayload(BaseModel):
    """Combined structured payload capturing all operational metrics from document synthesis."""
    weather_metrics: Optional[WeatherMetrics] = Field(
        default=None, description="Weather and storm metrics"
    )
    equipment_statuses: List[EquipmentStatus] = Field(
        default_factory=list, description="Status of equipment mentioned in the document"
    )
    plow_dispatches: List[PlowDispatch] = Field(
        default_factory=list, description="Plow dispatches extracted from the document"
    )
    road_closures: List[RoadClosure] = Field(
        default_factory=list, description="Road and highway closures extracted from the document"
    )


class DocumentSynthesisOutput(BaseModel):
    """Top-level output schema returned by the Gemini document synthesis model."""
    operational_summary: str = Field(
        description="Executive overview of plow routes, road closures, and winter storm impacts across Alaska corridors"
    )
    structured_payload: StructuredPayload = Field(
        description="Detailed structured JSON payload containing plow dispatches, closures, equipment, and weather"
    )
    risk_assessment: RiskAssessment = Field(
        description="Public safety risk assessment including risk rating, key hazards, and mitigation steps"
    )
