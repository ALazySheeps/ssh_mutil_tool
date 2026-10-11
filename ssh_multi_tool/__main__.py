import argparse


def main():
    parser = argparse.ArgumentParser(description="SSH 多终端启动工具 · Ubuntu desktop")
    parser.add_argument("--helper", action="store_true", help="内部终端会话模式")
    parser.add_argument("--socket", help="本机凭据服务路径")
    parser.add_argument("--token", help="一次性启动令牌（不含密码）")
    args = parser.parse_args()
    if args.helper:
        if not args.socket or not args.token: parser.error("helper模式需要 --socket 和 --token")
        from .helper import helper_main
        return helper_main(args.socket, args.token)
    from .gui import main as gui_main
    gui_main(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
