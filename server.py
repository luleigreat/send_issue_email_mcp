"""send_issue_email MCP Server.

将用户转人工时收集的邮箱与问题，通过 SMTP 发送到 .env 中配置的
固定服务邮箱（TARGET_EMAIL）。

暴露给 Agent 的工具：
    send_issue_email(email: str, question: str) -> str
"""

from __future__ import annotations

import html
import logging
import os
import re
import smtplib
import socket
import ssl
import sys
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

LOG_DIR = Path(__file__).resolve().parent / "log"
LOG_RETENTION_DAYS = 30
_LOG_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.log$")
_logger: logging.Logger | None = None


def _purge_old_logs(log_dir: Path, keep_days: int = LOG_RETENTION_DAYS) -> list[str]:
    """删除早于最近 keep_days 天的 YYYY-MM-DD.log（按文件名日期，含当天共 keep_days 天）。"""
    if not log_dir.is_dir():
        return []
    today = datetime.now().date()
    deleted: list[str] = []
    for path in log_dir.glob("*.log"):
        match = _LOG_NAME_RE.match(path.name)
        if not match:
            continue
        try:
            file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - file_date).days >= keep_days:
            try:
                path.unlink()
                deleted.append(path.name)
            except OSError:
                continue
    return deleted


class DailyAppendFileHandler(logging.Handler):
    """按天写入 log/YYYY-MM-DD.log，同一天多次启动为追加（attach）。"""

    def __init__(self, log_dir: Path, encoding: str = "utf-8") -> None:
        super().__init__()
        self.log_dir = log_dir
        self.encoding = encoding
        self._date: str | None = None
        self._stream = None
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_stream(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._date == today and self._stream is not None:
            return
        rolled = self._date is not None and self._date != today
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        path = self.log_dir / f"{today}.log"
        self._stream = open(path, "a", encoding=self.encoding)
        self._date = today
        if rolled:
            _purge_old_logs(self.log_dir)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._ensure_stream()
            assert self._stream is not None
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        finally:
            super().close()


def _setup_logging() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("issue_collector")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s [%(threadName)s] %(funcName)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = DailyAppendFileHandler(LOG_DIR)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    _logger = logger
    deleted = _purge_old_logs(LOG_DIR)
    if deleted:
        logger.info("已删除超过 %s 天的日志: %s", LOG_RETENTION_DAYS, ", ".join(sorted(deleted)))
    return logger


log = _setup_logging()

mcp = FastMCP("issue-collector")


def _get_config() -> dict:
    target = os.getenv("TARGET_EMAIL")
    if not target:
        raise ValueError("TARGET_EMAIL 未在 .env 中配置")
    return {
        "target": target,
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "465")),
        "username": os.getenv("SMTP_USERNAME", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "sender": os.getenv("SENDER_EMAIL", os.getenv("SMTP_USERNAME", "")),
        "sender_name": os.getenv("SENDER_NAME", "问题收集机器人"),
        "use_ssl": os.getenv("SMTP_USE_SSL", "true").lower() in ("1", "true", "yes"),
    }


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "tr",
    "table",
    "blockquote",
    "pre",
}


class _StripHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def get_text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self._chunks)).strip()


def _is_email(addr: str) -> bool:
    return bool(addr) and bool(_EMAIL_RE.match(addr))


def _preview(text: str, limit: int = 200) -> str:
    text = text.replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[:limit] + f"...(len={len(text)})"


def _split_html_question(question: str) -> tuple[str, str]:
    """拆出 HTML 前的纯文本前缀（如 [channel=...]）和后续 HTML 片段。"""
    match = _HTML_TAG_RE.search(question)
    if not match:
        return question.strip(), ""
    prefix = question[: match.start()].strip()
    html_part = question[match.start() :].strip()
    return prefix, html_part


def _html_to_plain(fragment: str) -> str:
    parser = _StripHTML()
    try:
        parser.feed(fragment)
        parser.close()
    except Exception:
        return _HTML_TAG_RE.sub("", fragment)
    return parser.get_text()


def _plain_to_html(text: str) -> str:
    escaped = html.escape(text, quote=False)
    return "<p>" + escaped.replace("\n", "<br>\n") + "</p>"


def _build_bodies(email: str, question: str, ts: str) -> tuple[str, str]:
    """组装纯文本与 HTML 正文。Agent 传入 HTML 时走 text/html，并保留纯文本兜底。"""
    prefix, html_part = _split_html_question(question)
    has_html = bool(html_part)
    question_plain = (
        (f"{prefix}\n{_html_to_plain(html_part)}" if prefix else _html_to_plain(html_part))
        if has_html
        else question
    )
    header_plain = (
        "收到一条用户转人工前提交的问题。\n\n"
        f"用户邮箱：{email}\n"
        f"提交时间：{ts}\n"
        "问题描述：\n"
    )
    plain = header_plain + question_plain + "\n"

    question_html = (
        (
            (f"<p>{html.escape(prefix)}</p>\n" if prefix else "")
            + html_part
        )
        if has_html
        else _plain_to_html(question)
    )
    html_body = (
        "<!DOCTYPE html><html><body>"
        "<p>收到一条用户转人工前提交的问题。</p>"
        f"<p><strong>用户邮箱：</strong>{html.escape(email)}<br>"
        f"<strong>提交时间：</strong>{html.escape(ts)}</p>"
        "<p><strong>问题描述：</strong></p>"
        f"{question_html}"
        "</body></html>"
    )
    return plain, html_body


def _close_smtp_in_background(smtp: smtplib.SMTP) -> None:
    """send_message 成功后立刻返回；QUIT/SSL 关闭放到后台，避免卡住 MCP 响应。"""

    def _run() -> None:
        t0 = time.monotonic()
        log.info("后台关闭 SMTP 开始 id=%s", id(smtp))
        try:
            smtp.quit()
            log.info("后台 SMTP QUIT 完成 id=%s elapsed=%.3fs", id(smtp), time.monotonic() - t0)
        except Exception as e:
            log.warning("后台 SMTP QUIT 失败，改为 close id=%s err=%s", id(smtp), e)
            try:
                smtp.close()
                log.info("后台 SMTP close 完成 id=%s elapsed=%.3fs", id(smtp), time.monotonic() - t0)
            except Exception as e2:
                log.warning("后台 SMTP close 失败 id=%s err=%s", id(smtp), e2)

    threading.Thread(target=_run, name="smtp-close", daemon=True).start()


def _open_smtp(cfg: dict) -> smtplib.SMTP:
    host, port = cfg["host"], cfg["port"]
    t0 = time.monotonic()
    if cfg["use_ssl"]:
        log.info("SMTP_SSL 连接中 host=%s port=%s timeout=15", host, port)
        conn: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=15)
        log.info("SMTP_SSL 已连接 elapsed=%.3fs", time.monotonic() - t0)
        return conn
    log.info("SMTP STARTTLS 连接中 host=%s port=%s timeout=15", host, port)
    conn = smtplib.SMTP(host, port, timeout=15)
    conn.starttls(context=ssl.create_default_context())
    log.info("SMTP STARTTLS 已完成 elapsed=%.3fs", time.monotonic() - t0)
    return conn


def send_email(email: str, question: str) -> str:
    """核心发送逻辑：校验参数 -> 组装邮件 -> 通过 SMTP 发送。

    send_message 成功后立即返回 MCP 结果，连接关闭在后台线程进行。
    """
    t_all = time.monotonic()
    log.info(
        "开始发信 user_email=%s question=%s",
        email,
        _preview(question or ""),
    )
    if not email or not question:
        log.warning("参数为空 email_empty=%s question_empty=%s", not email, not question)
        return "错误：邮箱和问题均不能为空。"
    if not _is_email(email):
        log.warning("用户邮箱格式无效 email=%s", email)
        return f"错误：用户邮箱格式无效：{email}"

    try:
        cfg = _get_config()
    except ValueError as e:
        log.error("读取配置失败: %s", e)
        return f"错误：{e}"

    log.info(
        "SMTP 配置 host=%s port=%s ssl=%s user=%s sender=%s target=%s",
        cfg["host"],
        cfg["port"],
        cfg["use_ssl"],
        cfg["username"],
        cfg["sender"],
        cfg["target"],
    )

    if not _is_email(cfg["target"]):
        log.warning("目标服务邮箱格式无效 target=%s", cfg["target"])
        return f"错误：目标服务邮箱格式无效：{cfg['target']}"
    if not cfg["sender"]:
        log.error("发件人未配置")
        return "错误：发件人 SENDER_EMAIL / SMTP_USERNAME 未配置。"

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plain_body, html_body = _build_bodies(email, question, ts)
    has_html = bool(_split_html_question(question)[1])
    msg = EmailMessage()
    msg["Subject"] = f"[问题收集] 来自用户 {email} ({ts})"
    msg["From"] = f"{cfg['sender_name']} <{cfg['sender']}>"
    msg["To"] = cfg["target"]
    msg["Reply-To"] = email
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")
    log.debug("邮件已组装 subject=%s html=%s", msg["Subject"], has_html)

    smtp: smtplib.SMTP | None = None
    try:
        smtp = _open_smtp(cfg)
        if cfg["username"] and cfg["password"]:
            t_login = time.monotonic()
            smtp.login(cfg["username"], cfg["password"])
            log.info("SMTP 登录成功 elapsed=%.3fs", time.monotonic() - t_login)
        else:
            log.info("未配置 SMTP 用户名/密码，跳过 login")
        t_send = time.monotonic()
        smtp.send_message(msg)
        log.info(
            "SMTP send_message 成功 to=%s elapsed=%.3fs total=%.3fs，即将后台关闭连接",
            cfg["target"],
            time.monotonic() - t_send,
            time.monotonic() - t_all,
        )
        _close_smtp_in_background(smtp)
        smtp = None
        return f"成功：问题已发送至服务邮箱 {cfg['target']}。"
    except (smtplib.SMTPException, socket.error, ssl.SSLError, TimeoutError, OSError) as e:
        log.exception("邮件发送失败 elapsed=%.3fs err=%s", time.monotonic() - t_all, e)
        if smtp is not None:
            _close_smtp_in_background(smtp)
            smtp = None
        return f"错误：邮件发送失败 - {e}"


@mcp.tool()
def send_issue_email(email: str, question: str) -> str:
    """将用户转人工时收集的邮箱与问题发送到指定的服务邮箱。

    Args:
        email: 用户的邮箱地址（来自对话收集变量 $邮箱）。
        question: 用户的问题/诉求（来自对话收集变量 $问题）。
    """
    log.info("MCP 工具 send_issue_email 被调用")
    result = send_email(email, question)
    log.info("MCP 工具返回: %s", result)
    return result


class _AcceptCompatMiddleware:
    """智齿等客户端常只带 Content-Type，缺 MCP 要求的 Accept，这里补上以免 406。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = list(scope.get("headers") or [])
        accept = b""
        rest = []
        for key, value in headers:
            if key == b"accept":
                accept = value
            else:
                rest.append((key, value))
        accept_l = accept.lower()
        extras = []
        if b"application/json" not in accept_l:
            extras.append(b"application/json")
        if b"text/event-stream" not in accept_l:
            extras.append(b"text/event-stream")
        if extras:
            patched = b", ".join(extras) if not accept else accept + b", " + b", ".join(extras)
            rest.append((b"accept", patched))
            scope = {**scope, "headers": rest}

        method = scope.get("method")
        path = scope.get("path")
        client = scope.get("client")
        t0 = time.monotonic()
        status_code: int | None = None
        log.info("HTTP 请求开始 method=%s path=%s client=%s", method, path, client)

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log.exception(
                "HTTP 请求异常 method=%s path=%s elapsed=%.3fs",
                method,
                path,
                time.monotonic() - t0,
            )
            raise
        finally:
            log.info(
                "HTTP 请求结束 method=%s path=%s status=%s elapsed=%.3fs",
                method,
                path,
                status_code,
                time.monotonic() - t0,
            )


if __name__ == "__main__":
    from starlette.middleware import Middleware

    transport = os.getenv("MCP_TRANSPORT", "stdio")
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))
    path = os.getenv("MCP_PATH", "/")
    log.info(
        "启动 MCP transport=%s host=%s port=%s path=%s log_dir=%s",
        transport,
        host,
        port,
        path,
        LOG_DIR,
    )
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # 智齿对配置的 URL 发 POST /，必须把 MCP 挂在根路径，并用 JSON 响应。
        mcp.run(
            transport=transport,
            host=host,
            port=port,
            path=path,
            json_response=True,
            stateless_http=True,
            middleware=[Middleware(_AcceptCompatMiddleware)],
        )
