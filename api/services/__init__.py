"""Query + business logic helpers used by routers.

Routers stay thin; services own SQL composition + transformation
between ORM rows and Pydantic schemas. Keep imports cheap — the API
process boots fresh on every uvicorn reload during dev.
"""
