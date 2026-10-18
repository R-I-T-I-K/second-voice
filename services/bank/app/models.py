from pydantic import BaseModel, Field


class DeviceContext(BaseModel):
    active_call_minutes: int = 0
    remote_access_app_installed_minutes_ago: int | None = None
    payee_added_minutes_ago: int | None = None
    seconds_on_pay_screen: int = 0


class PayRequest(BaseModel):
    customer_id: str
    payee_vpa: str
    payee_name: str | None = None
    amount: float = Field(gt=0)
    device: DeviceContext = DeviceContext()
    # Demo-only knobs. The registry stub is "simulated, external" in the plan.
    registry_hit: bool = False


class AnswerRequest(BaseModel):
    text: str | None = None
    option: str | None = None


class ActionRequest(BaseModel):
    action: str  # cancel | override | trusted_contact | release


class ValidateRequest(BaseModel):
    vpa: str


class CreditRequest(BaseModel):
    vpa: str
    amount: float
    from_bank: str
    ref: str


class OutcomeRequest(BaseModel):
    outcome: str  # confirmed_scam | false_alarm
