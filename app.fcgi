#!/data/project/checker/venv/bin/python
import flup.server.fcgi
import werkzeug.exceptions
import werkzeug.wsgi

import checker

static_routes = {'/static': '/data/project/checker/static'}
app_routes = {'/': checker.app}

try:
  import testing.checker
except ImportError:
  app_routes['/testing'] = testing.checker.app
else:
  app_routes['/testing'] = checker.app

default_handler = werkzeug.exceptions.NotFound
static = werkzeug.wsgi.SharedDataMiddleware(default_handler, static_routes)
app = werkzeug.wsgi.DispatcherMiddleware(static, app_routes)
flup.server.fcgi.WSGIServer(app).run()
