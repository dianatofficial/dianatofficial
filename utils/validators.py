
# Revision 3.38
# Schema Validation
def validate_payload(data: dict) -> bool:
    return isinstance(data, dict)
