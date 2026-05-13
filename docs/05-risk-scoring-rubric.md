# Risk Scoring Rubric — ClinicFlow

The risk scorer is a pure function with NO database calls.
The service layer fetches all required data and passes it in.
This makes the function fully deterministic and unit-testable.

Signature:
  calculate_risk(appointment, patient_history, context) -> RiskResult

## Inputs required

appointment:
  scheduled_at        datetime
  appointment_type    str  (initial_eval / follow_up / consultation)
  duration_minutes    int

patient_history:
  past_appointments   list of {scheduled_at, status, completed_at}
  lifetime_completed  int   (total completed appointments ever)
  lifetime_no_shows   int   (total no-shows ever)
  has_phone           bool  (phone number on file)

context:
  now                 datetime  (current time, for lead-time calculation)
  weather_alert       bool      (severe weather alert active for that day)

## Scoring rules — implement ALL of these exactly

### Additions (increase no-show risk)
| Points | Condition                                                     |
|--------|---------------------------------------------------------------|
| +30    | Patient has 2 or more no-shows in the past 90 days           |
| +20    | Patient has exactly 1 no-show in the past 90 days            |
| +15    | Appointment booked more than 14 days in advance              |
| +10    | Slot is Monday 8:00-10:00am OR Friday after 16:00            |
| +10    | No phone number on file (cannot send SMS reminder)           |
| +8     | First-ever appointment with this clinic (no history at all)  |
| +15    | weather_alert is True                                        |
| +5     | appointment_type is initial_eval                             |

### Subtractions (decrease no-show risk)
| Points | Condition                                                     |
|--------|---------------------------------------------------------------|
| -20    | Patient confirmed a previous reminder within 48hrs of booking |
| -15    | Patient has 5+ lifetime completed appointments and 0 no-shows |
| -10    | This is a follow-up within 7 days of a prior completed visit  |
| -5     | Patient has 2-4 lifetime completed appointments and 0 no-shows|

## Score constraints
- Minimum score: 0    (clamp — never go negative)
- Maximum score: 100  (clamp — never exceed)

## Bucketing
| Bucket  | Score range | Intervention                                      |
|---------|-------------|---------------------------------------------------|
| low     | 0-25        | Standard reminder 24hrs before                    |
| medium  | 26-50       | Personalized LLM SMS at 48hrs and 24hrs before    |
| high    | 51+         | Multi-touch at 72hrs, 48hrs, 24hrs + waitlist prep|

## Output schema (Pydantic v2)
class RiskResult(BaseModel):
    score: int                              # 0-100 inclusive
    bucket: Literal["low","medium","high"]
    factors: list[str]                      # human-readable applied rules
    scored_at: datetime

## Required unit tests
- Patient with zero history scores 8 (first-ever appointment only)
- Patient with 2 no-shows in 90 days gets +30 applied
- Score is clamped to 0 — never goes negative
- Score is clamped to 100 — never exceeds
- Correct bucket at boundaries: 25=low, 26=medium, 50=medium, 51=high
- Multiple factors stack correctly and independently
- Subtraction cannot reduce score below 0
- Weather alert adds +15 on top of other factors
