import logging
import pickle

from socketio.pubsub_manager import PubSubManager

try:
    import uwsgi
except ImportError:
    uwsgi = None

logger = logging.getLogger(__name__)


class UWSGIManager(PubSubManager):
    """Uwsgi based client manager.

    This class implements a UWSGI backend for event sharing across multiple
    processes.

    To use a uWSGI backend, initialize the :class:`Server` instance as
    follows::

        server = socketio.Server(client_manager=UWSGIManager())

    Or with Flask-Socketio::

        socketio = SocketIO(app, client_manager=UWSGIManager())

    :param cache: The name of the caching instance to connect to, for
        example: mycache@localhost:3031, defaults to an empty string, which
        means uWSGI will use the first cache instance initialized.
        If the cache is in the same instance as the werkzeug app,
        you only have to provide the name of the cache.
    :param cache_timeout: The default timeout in seconds.
    :param debug: set logger to DEBUG level

    """
    name = 'uwsgi'
    default_cache_timeout = 86400  # 1 day
    short_cache_timeout = 2  # seconds

    def __init__(self, channel='socketio', cache='', cache_timeout=default_cache_timeout, debug=False):
        super().__init__(channel=channel, write_only=True, logger=logger)
        self._worker_id = None
        self.has_workers = False
        self.debug = debug
        self.cache_store = cache
        self.cache_timeout = cache_timeout
        self.cache_sid_key = '%s_sid_%%s' % self.channel  # worker_id
        self.cache_msg_key = '%s_msg_%%s_%%s' % self.channel  # worker_id, message_id
        self._init_configuration()

    def _init_configuration(self):
        if uwsgi is None:
            raise RuntimeError('You are not running under uWSGI')
        if 'cache2' not in uwsgi.opt:
            raise RuntimeError('You must enable cache2 in uWSGI configuration: https://uwsgi-docs.readthedocs.io/en/latest/Caching.html')
        logger.setLevel(logging.DEBUG if self.debug else logger.WARNING)
        self._register_signals()

    @property
    def worker_id(self):
        if self._worker_id is None:
            self._worker_id = uwsgi.worker_id()
        return self._worker_id

    def _register_signals(self):
        if uwsgi.numproc > 1:
            self.has_workers = True
            # signal 0 will call all workers
            uwsgi.register_signal(0, 'workers', self._check_msg_in_cache)
            # others signals match each worker independently
            for i in range(1, uwsgi.numproc + 1):
                uwsgi.register_signal(i, 'worker%s' % i, self._check_msg_in_cache)

    def _cache_sid_add(self, sid):
        logger.debug('Set SID from worker %s - %s' % (self.worker_id, sid))
        uwsgi.cache_update(self.cache_sid_key % sid, pickle.dumps(self.worker_id), self.cache_timeout, self.cache_store)

    def _cache_sid_del(self, sid):
        logger.debug('Delete SID from worker %s - %s' % (self.worker_id, sid))
        uwsgi.cache_del(self.cache_sid_key % sid, self.cache_store)

    def _cache_worker_id(self, sid):
        """ Get worker_id from sid else return the 0 id
        :type sid: str
        :rtype: int
        """
        wid = uwsgi.cache_get(self.cache_sid_key % sid, self.cache_store)
        return 0 if wid is None else pickle.loads(wid)

    def _cache_add_msg(self, worker_id, data):
        msg_key = None
        for msg_id in range(0, 10):
            msg_key = self.cache_msg_key % (worker_id, msg_id)
            if uwsgi.cache_exists(msg_key, self.cache_store) is None:
                break
            msg_key = None
        if msg_key is None:
            msg_key = self.cache_msg_key % (worker_id, 0)
            logger.warning('Cached queue for worker %s is full, overwrite data' % worker_id)
        logger.debug('Store message from worker %s to %s - %s' % (self.worker_id, msg_key, data))
        return uwsgi.cache_update(msg_key,
                                  pickle.dumps(data),
                                  self.cache_timeout if worker_id else self.short_cache_timeout,
                                  self.cache_store)

    def _cache_get_msg(self, worker_id):
        for msg_id in range(0, 10):
            msg_key = self.cache_msg_key % (worker_id, msg_id)
            msg = uwsgi.cache_get(msg_key, self.cache_store)
            if msg is not None:
                print('Get message from worker %s - %s' % (self.worker_id, msg_key))
                if worker_id:
                    # delete message if worker_id is different from 0, else `short_cache_timeout` will do the job
                    uwsgi.cache_del(msg_key, self.cache_store)
                yield msg

    def connect(self, sid, namespace):
        """ Register SID location from current worker
        :type sid: str
        :type namespace: str
        """
        if self.has_workers and namespace == '/':
            logger.debug('Connected on worker %s - %s' % (self.worker_id, sid))
            self._cache_sid_add(sid)
        super().connect(sid, namespace)

    def disconnect(self, sid, namespace):
        """ Unregister SID location from current worker
        :type sid: str
        :type namespace: str
        """
        if self.has_workers and namespace == '/':
            logger.debug('Disconnected from worker %s - %s' % (self.worker_id, sid))
            self._cache_sid_del(sid)
        super().disconnect(sid, namespace)

    def _publish(self, data):
        """" Dispatch messages accross workers """
        if self.has_workers:
            worker_id = self._cache_worker_id(data['room'])
            if self.worker_id == worker_id:
                logger.debug('Same worker (%s) relay msg internally' % worker_id)
                self._internal_emit(data)
            else:
                logger.debug('Other worker than me (%s) emit to worker %s' % (self.worker_id, worker_id))
                self._cache_add_msg(worker_id, data)
                uwsgi.signal(worker_id)
        else:
            self._internal_emit(data)

    def _check_msg_in_cache(self, signum):
        """ Registered function from uWSGI Signal Framework
        :type signum: int
        :param signum: the worker id. If 0, all workers will try to process the messages
        """
        for msg in self._cache_get_msg(signum):
            self._internal_emit(pickle.loads(msg))

    def _internal_emit(self, data):
        """ Process data like the `PubSubManager` in `_thread` method """
        logger.debug('Emit from worker %s message %s' % (self.worker_id, data))
        if data and 'method' in data:
            if data['method'] == 'emit':
                self._handle_emit(data)
            elif data['method'] == 'callback':
                self._handle_callback(data)
            elif data['method'] == 'disconnect':
                self._handle_disconnect(data)
            elif data['method'] == 'close_room':
                self._handle_close_room(data)

    def _listen(self):
        """ Must be implemented """
        pass
