# python-socketio-uwsgi-manager
The purpose of this manager is to handle messages and events accross uWSGI local workers/processes.

**Context**

Socketio sessions are dispatched on different processes, this manager store this information in uWSGI cache and route all events to the concerned worker.
For any reasons if a worker was not registered for a given SID, to avoid losing event we forward it to all workers (fallback solution).
To manage concurrency, events are stored in cache slots dedicated to each workers and they are incremented if a slot is not available.

Although the cache address can be a remote server, uWSGI signals are for internal use only and do not warn other potential uWSGI servers.  
It would be possible to accomplish this if uWSGI signals could be sent to the target servers but this is not the case here.

A simpler solution to distribute events would be to use the RPC system of uWSGI through the workers but this feature is not available, I made a request about it on the uWSGI repository.

**Prerequisite**

uWSGI cache must be enabled to store events and sids.

See uWSGI Caching Framework options [here](https://uwsgi-docs.readthedocs.io/en/latest/Caching.html)
```ini
uwsgi --cache2 name=default,items=100
```

**Usage**

[python-socketio](https://github.com/miguelgrinberg/python-socketio)
```python
server = socketio.Server(client_manager=UWSGIManager())
```

[Flask-SocketIO](https://github.com/miguelgrinberg/Flask-SocketIO)
```python
socketio = SocketIO(app, client_manager=UWSGIManager())
```

**Options**
```python
UWSGIManager(channel='socketio', cache='', cache_timeout=default_cache_timeout, debug=False)
```
  * **cache**: cache name (empty string means uWSGI will use the first cache instance initialized)
  * **cache_timeout**: mainly for cahcing sids, cached events are deleted when workers unstack them.
  * **debug**: enabling debug mode
