"""Flask extension singletons.

They are instantiated unbound here and attached to the application inside
:func:`irrigation.create_app` so that the package stays importable without a
running application context.
"""

from __future__ import annotations

from flask_login import LoginManager
from flask_pymongo import PyMongo

mongo = PyMongo()
login_manager = LoginManager()
