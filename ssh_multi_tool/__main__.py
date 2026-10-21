import argparse


def main():
    parser = argparse.ArgumentParser(description="SSH 多终端启动工具 · Ubuntu desktop")
    parser.add_argument("--helper", action="store_true", help="内部终端会话模式")
    parser.add_argument("--socket", help="本机凭据服务路径")
    parser.add_argument("--token", help="一次性启动令牌（不含密码）")
    parser.add_argument("--focus-terminal", action="store_true", help="内部标签页定位模式")
    parser.add_argument("--title", help="待定位标签页标题")
    parser.add_argument("--pid", type=int, help="终端服务进程标识")
    parser.add_argument("--local-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--command", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.local_child:
        if not args.command: parser.error("本机会话需要 --command")
        from .local import child_main
        child_main(args.command)
    if args.focus_terminal:
        if not args.title or not args.pid or args.pid <= 0: parser.error("定位模式需要 --title 和有效的 --pid")
        from .terminal_focus import focus_terminal
        return 0 if focus_terminal(args.pid, args.title) else 1
    if args.helper:
        if not args.socket or not args.token: parser.error("helper模式需要 --socket 和 --token")
        from .helper import helper_main
        return helper_main(args.socket, args.token)
    from .gui import main as gui_main
    gui_main(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
