"""
Entry point pre Kodi service addon.
Spusti HTTP proxy server na localhost s nahodnym portom.
Port sa zapise do Home Window property 'sc.helper.port'.
"""
try:
    import xbmc
    import xbmcaddon
    import xbmcgui
    _KODI = True
except ImportError:
    _KODI = False

from resources.lib import start_server


def _teardown(server, session_manager):
    """Rychly cleanup — Kodi zabije addon ak sa neukonci do ~5s a caka
    na join VSETKYCH child threadov (daemon flag nestaci v Kodi Python host).
    Poradie: signal stop -> shutdown HTTP -> close socket -> explicit join threads.
    """
    # 1. Close active upstream connections — pump loopy v blokujucom
    # upstream.read() dostanu OSError a ukoncia sa (inak by Kodi cakal na ne).
    try:
        session_manager.close_all_upstreams()
    except Exception:
        pass
    # 2. Signal + wait background threads (Event-based, ms)
    try:
        session_manager.stop_reprobe_thread()
    except Exception:
        pass
    try:
        session_manager.stop_cleanup_thread()
    except Exception:
        pass
    try:
        session_manager.close_all()
    except Exception:
        pass
    # 2. Stop guard thread (attached na server z start_server)
    try:
        if getattr(server, '_guard_stop', None):
            server._guard_stop.set()
        if getattr(server, '_guard_thread', None):
            server._guard_thread.join(timeout=1)
    except Exception:
        pass
    # 3. Shutdown HTTP loop + zavri listening socket
    try:
        server.shutdown()
    except Exception:
        pass
    try:
        server.server_close()
    except Exception:
        pass
    # 4. Explicit join serve_forever thread (Kodi caka naň)
    try:
        if getattr(server, '_sf_thread', None):
            server._sf_thread.join(timeout=2)
    except Exception:
        pass


def main():
    server, session_manager = start_server(port=0)
    port = server.server_address[1]

    if _KODI:
        addon = xbmcaddon.Addon()
        xbmcgui.Window(10000).setProperty('sc.helper.port', str(port))
        addon.setSetting('port', str(port))
        xbmc.log('[service.sc.helper] Started on port {0}'.format(port), xbmc.LOGINFO)
        monitor = xbmc.Monitor()
        try:
            monitor.waitForAbort()
        finally:
            try:
                xbmcgui.Window(10000).clearProperty('sc.helper.port')
            except Exception:
                pass
            _teardown(server, session_manager)
            xbmc.log('[service.sc.helper] Stopped', xbmc.LOGINFO)
    else:
        import signal
        print('SC Helper server on http://127.0.0.1:{0}'.format(port))
        try:
            signal.pause()
        finally:
            _teardown(server, session_manager)


def daemon():
    import json
    import os
    import sys

    server, _ = start_server(port=0)
    port = server.server_address[1]
    url = 'http://127.0.0.1:{0}'.format(port)

    pid = os.fork()
    if pid > 0:
        sys.stdout.write(json.dumps({'url': url, 'port': port, 'pid': pid}) + '\n')
        sys.stdout.flush()
        sys.exit(0)

    os.setsid()
    sys.stdin.close()
    import signal
    signal.pause()


if __name__ == '__main__':
    import sys
    if '--daemon' in sys.argv:
        daemon()
    else:
        main()
