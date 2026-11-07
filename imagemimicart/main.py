import getpass
import sys

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import profile, theme
from .icon import make_icon
from .main_window import MainWindow, start_path_from_args

# 2 つ目を起動したら、先に動いているほうを前に出して自分は終わる。
# 同じジョブを 2 つのアプリが取り合わないようにするため。
_SERVER_NAME = f"ImageMimicArt-{getpass.getuser()}"


def _notify_running_instance() -> bool:
    socket = QLocalSocket()
    socket.connectToServer(_SERVER_NAME)
    if not socket.waitForConnected(500):
        return False
    if sys.platform == "win32":
        # 前面に出るのを Windows に拒まれないよう、こちらから許可しておく
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    socket.write(b"show")
    socket.flush()
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


def _start_server(window: MainWindow) -> QLocalServer:
    server = QLocalServer(window)
    if not server.listen(_SERVER_NAME):
        QLocalServer.removeServer(_SERVER_NAME)
        server.listen(_SERVER_NAME)

    def on_connection():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnected.connect(conn.deleteLater)
        window.bring_to_front()

    server.newConnection.connect(on_connection)
    return server


def main():
    app = QApplication(sys.argv)
    if _notify_running_instance():
        sys.exit(0)
    app.setApplicationName("ImageMimicArt")
    app.setWindowIcon(make_icon())
    theme.apply(app)
    merged = profile.setup()  # 設定の置き場所を決め、初めてなら共有の設定に移す
    notice = ("共有の設定: " + "。".join(merged)) if merged else ""
    window = MainWindow(start_path_from_args(), notice)
    _start_server(window)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
