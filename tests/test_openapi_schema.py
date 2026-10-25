#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Gluesync Scheduler Module (aka Chronos) is dual-licensed under the following licenses:
 *
 * 1. GNU General Public License (GPL) Version 3
 *    You may use, modify, and distribute this software under the terms of the GPL v3.
 *    See the LICENSE-GPL file or <http://www.gnu.org/licenses/gpl-3.0.html> for details.
 *    This option is available at no cost, but any derivative works must also be licensed under GPL v3.
 *
 * 2. MOLO17 Commercial License
 *    Alternatively, you may use this software under the MOLO17 Commercial License,
 *    which includes a warranty and permits proprietary use. Contact MOLO17 at info@molo17.com
 *    for licensing terms and conditions.
 *
 * You must choose one of these licenses to use this software. Using this software implies
 * acceptance of one of these licenses. See the accompanying LICENSE files or contact
 * MOLO17 for more information.
 *
 * Copyright (C) 2025 MOLO17. All rights reserved.
"""

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_openapi_schema_generates():
    """Regression: the jsonable_encoder override in app.py used to recurse forever,
    breaking /api/openapi.json (and therefore /api/docs and /api/redoc)."""
    from gluesync_scheduler.core.app import app

    app.openapi_schema = None
    schema = app.openapi()

    assert isinstance(schema, dict)
    assert schema["paths"]
    documented_paths = {
        route.path
        for route in app.routes
        if getattr(route, "include_in_schema", False) and hasattr(route, "methods")
    }
    assert documented_paths <= set(schema["paths"])


def test_openapi_json_endpoint_returns_200():
    from gluesync_scheduler.core.app import app

    app.openapi_schema = None
    response = TestClient(app).get("/api/openapi.json")

    assert response.status_code == 200
    assert response.json()["paths"]
