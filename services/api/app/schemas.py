# Purpose: define Pydantic request/response models from contracts/openapi.yaml.
# Call: import from routes and domain adapters after implementation.
# Output constraint: exact OpenAPI wire fields; camelCase, decimal strings, explicit nulls, key+period validation; do not trust client validationStatus/verified.
