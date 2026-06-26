# Product Nova API Reference

## Authentication
Nova API requests require a bearer token created from the developer console. Tokens can be scoped to read-only, write, or admin permissions. Tokens expire after 90 days by default.

## Rate Limits
The standard API limit is 600 requests per minute per workspace. Enterprise customers can request a dedicated limit after completing capacity review.

--- page ---

## Error Handling
The API returns structured errors with `code`, `message`, and `request_id`. Clients should retry `429` and `5xx` responses using exponential backoff with jitter.
