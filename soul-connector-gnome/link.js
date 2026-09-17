// Client of the daemon's level stream (soul_connector/server.py), spoken over
// WebSocket against ws://127.0.0.1:8765.
//
// It reuses the same server and the same JSON protocol the pywebview
// soul-connector already consumes, without touching a line of the daemon: to
// Tero this is just one more client of the stream, exactly as CLAUDE.md says
// it has to be ("El daemon tiene que funcionar sin el soul-connector. La
// ventana es un cliente opcional del stream de niveles"). That is what makes
// this extension removable with no consequences.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Soup from 'gi://Soup?version=3.0';

const URL = 'ws://127.0.0.1:8765';
const RETRY_MS = 1000;

export class Link {
    /**
     * @param onMessage receives the already parsed JSON object
     * @param onConnection receives true/false when the connection with the
     *   daemon changes (not on every failed retry)
     */
    constructor(onMessage, onConnection = () => {}) {
        this._onMessage = onMessage;
        this._onConnection = onConnection;
        this._connected = null;
        this._session = new Soup.Session();
        this._connection = null;
        this._cancellable = null;
        this._retryId = 0;
        this._closed = false;
        this._decoder = new TextDecoder();
    }

    connect() {
        if (this._closed)
            return;
        this._cancellable = new Gio.Cancellable();
        const message = new Soup.Message({
            method: 'GET',
            uri: GLib.Uri.parse(URL, GLib.UriFlags.NONE),
        });
        this._session.websocket_connect_async(
            message, null, null, GLib.PRIORITY_DEFAULT, this._cancellable,
            (session, res) => this._onConnected(session, res)
        );
    }

    _onConnected(session, res) {
        if (this._closed)
            return;
        let connection;
        try {
            connection = session.websocket_connect_finish(res);
        } catch (e) {
            // The normal case is the daemon not being up yet: not an error
            // worth logging on every retry.
            this._notifyConnection(false);
            this._scheduleRetry();
            return;
        }
        this._connection = connection;
        this._notifyConnection(true);
        connection.connect('message', (_c, type, data) => {
            if (type !== Soup.WebsocketDataType.TEXT)
                return;
            try {
                this._onMessage(JSON.parse(this._decoder.decode(data.toArray())));
            } catch (e) {
                // A malformed message cannot bring the connection down.
            }
        });
        connection.connect('closed', () => {
            this._connection = null;
            this._notifyConnection(false);
            this._scheduleRetry();
        });
        connection.connect('error', () => {
            this._connection = null;
        });
    }

    _notifyConnection(connected) {
        if (this._closed || this._connected === connected)
            return;
        this._connected = connected;
        this._onConnection(connected);
    }

    _scheduleRetry() {
        if (this._closed || this._retryId)
            return;
        this._retryId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, RETRY_MS, () => {
            this._retryId = 0;
            this.connect();
            return GLib.SOURCE_REMOVE;
        });
    }

    destroy() {
        this._closed = true;
        if (this._retryId) {
            GLib.Source.remove(this._retryId);
            this._retryId = 0;
        }
        if (this._cancellable) {
            this._cancellable.cancel();
            this._cancellable = null;
        }
        if (this._connection) {
            this._connection.close(Soup.WebsocketCloseCode.NORMAL, null);
            this._connection = null;
        }
        this._session = null;
    }
}
