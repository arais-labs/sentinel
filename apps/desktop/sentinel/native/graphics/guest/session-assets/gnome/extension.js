import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const read = path => new TextDecoder().decode(GLib.file_get_contents(path)[1]);

export default class SessionReadiness extends Extension {
    enable() {
        // A connected socket is ready before GNOME removes its invisible,
        // input-blocking startup cover. Publish the actual lifecycle signal.
        if (typeof Main.layoutManager._startingUp !== 'boolean')
            throw new Error('Unsupported GNOME startup lifecycle');
        if (Main.layoutManager._startingUp) {
            this._signal = Main.layoutManager.connect('startup-complete', () => {
                Main.layoutManager.disconnect(this._signal);
                this._signal = 0;
                this._publish();
            });
        } else {
            this._publish();
        }
    }

    _publish() {
        const directory = `${GLib.get_user_runtime_dir()}/sentinel-desktop`;
        const source = `${directory}/owner.json`;
        if (!GLib.file_test(source, GLib.FileTest.EXISTS))
            return; // Inert outside a Sentinel-owned login.
        const owner = JSON.parse(read(source));
        const credentials = new Gio.Credentials();
        if (owner.uid !== credentials.get_unix_user() || !owner.token)
            throw new Error('GNOME startup owner mismatch');
        const stat = read('/proc/self/stat');
        const identity = stat.slice(stat.lastIndexOf(')') + 1).trim().split(/\s+/)[19];
        const value = {
            schema: 1, token: owner.token, session_id: owner.session_id, uid: owner.uid,
            shell: {pid: credentials.get_unix_pid(), identity},
        };
        this._marker = Gio.File.new_for_path(`${directory}/gnome-ready.json`);
        this._marker.replace_contents(JSON.stringify(value), null, false,
            Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        this._published = value;
    }

    disable() {
        if (this._signal)
            Main.layoutManager.disconnect(this._signal);
        this._signal = 0;
        if (this._marker) {
            try {
                const current = JSON.parse(new TextDecoder().decode(this._marker.load_contents(null)[1]));
                if (current.token === this._published.token &&
                    current.session_id === this._published.session_id &&
                    current.uid === this._published.uid &&
                    current.shell?.pid === this._published.shell.pid &&
                    current.shell?.identity === this._published.shell.identity)
                    this._marker.delete(null);
            } catch (error) {
                if (!error.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.NOT_FOUND))
                    throw error;
            }
            this._marker = null;
            this._published = null;
        }
    }
}
