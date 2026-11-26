"""AGV 제어 노드의 ROS 2 실행 진입점."""

import rclpy
import signal
from threading import Event, Thread
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor

from .agv_controller_node import AgvControllerNode


def run_with_panel(node):
    """Qt는 메인 스레드에 두고, 제어 콜백은 별도 executor에서 실행한다."""
    from PyQt5.QtWidgets import QApplication
    from .operator_ui import Panel

    app = QApplication([])
    panel = Panel(node.debug, node.config["job_feedback_topic"])
    executor = SingleThreadedExecutor(context=node.context)
    executor.add_node(node)
    stop = Event()
    failures = []

    def control_loop():
        try:
            while rclpy.ok() and not stop.is_set():
                executor.spin_once(timeout_sec=0.1)
        except ExternalShutdownException:
            pass
        except Exception as exc:
            failures.append(exc)

    worker = Thread(target=control_loop, name="agv-control")
    # SIGINT가 Qt 슬롯 실행 도중 KeyboardInterrupt를 발생시키지 않도록
    # 종료 요청만 기록하고, 타이머와 제어 루프에서 순서대로 종료한다.
    previous_sigint = signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker.start()
    panel.timer.timeout.connect(lambda: panel.close() if not worker.is_alive() else None)
    try:
        panel.show()
        app.exec_()
        # 창을 닫아도 이미 진행 중인 차량 제어는 계속한다.
        while worker.is_alive() and rclpy.ok():
            worker.join(0.2)
    finally:
        stop.set()
        worker.join()
        executor.shutdown()
        signal.signal(signal.SIGINT, previous_sigint)
    if failures:
        raise failures[0]


def main(args=None) -> None:
    """노드를 실행하고 종료 시 PLC 통신 스레드까지 안전하게 정리한다."""
    rclpy.init(args=args)
    node = None
    try:
        node = AgvControllerNode()
        if node.debug:
            run_with_panel(node)
        else:
            rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.close()
            node.destroy_node()
        rclpy.try_shutdown()
