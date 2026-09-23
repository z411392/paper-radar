import html

from libs.delivery.dtos.digest_preview import SelectedDigestItem


class DigestPreviewTemplate:
    @staticmethod
    def render(
        items: tuple[SelectedDigestItem, ...],
        *,
        settings_url: str | None,
    ) -> tuple[str, str, str]:
        subject = f"Paper Radar｜每日精選 {len(items)} 篇"

        text_lines = ["Paper Radar 每日精選", ""]
        html_items: list[str] = []
        for index, item in enumerate(items, start=1):
            domains = "、".join(item.domains)
            text_lines.extend(
                [
                    f"{index}. {item.title}",
                    f"領域：{domains}",
                    *[f"- {line}" for line in item.plain_language],
                ]
            )
            if item.source_url is not None:
                text_lines.append(f"來源：{item.source_url}")
            text_lines.append("")

            title = html.escape(item.title)
            domain_html = html.escape(domains)
            paragraphs = "".join(f"<p>{html.escape(line)}</p>" for line in item.plain_language)
            source = ""
            if item.source_url is not None:
                href = html.escape(item.source_url, quote=True)
                source = f'<p><a href="{href}">查看來源</a></p>'
            html_items.append(
                f"<li><h2>{title}</h2><p>領域：{domain_html}</p>{paragraphs}{source}</li>"
            )

        if settings_url is not None:
            text_lines.extend(["設定：" + settings_url, ""])
            settings = html.escape(settings_url, quote=True)
            settings_html = f'<p><a href="{settings}">調整 Paper Radar 設定</a></p>'
        else:
            settings_html = ""

        text_body = "\n".join(text_lines).rstrip() + "\n"
        html_body = (
            "<!doctype html><html><body><h1>Paper Radar 每日精選</h1>"
            f"<ol>{''.join(html_items)}</ol>{settings_html}</body></html>"
        )
        return subject, text_body, html_body
