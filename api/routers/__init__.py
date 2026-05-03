"""HTTP route definitions. One module per resource.

Routers should stay thin — query composition lives in
:mod:`api.services`. Each module exports a top-level
``router = APIRouter(...)`` that ``api.main`` includes with a
``/api/<resource>`` prefix.
"""
