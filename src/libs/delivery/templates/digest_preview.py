import html

from libs.delivery.dtos.digest_preview import SelectedDigestItem


class DigestPreviewTemplate:
    DOMAIN_LABELS = {
        "deep_learning": "深度學習",
        "machine_learning": "機器學習",
        "statistics": "統計",
        "badminton": "羽球",
        "male_reproductive_urology": "男性生殖學／泌尿科醫學",
    }

    @classmethod
    def _domain_label(cls, items: tuple[SelectedDigestItem, ...]) -> str | None:
        domains = {
            item.domains[0]
            for item in items
            if item.item_kind == "paper" and len(item.domains) == 1
        }
        paper_count = sum(item.item_kind == "paper" for item in items)
        if paper_count != len(items) or len(domains) != 1:
            return None
        return cls.DOMAIN_LABELS.get(next(iter(domains)))

    @classmethod
    def _heading(cls, items: tuple[SelectedDigestItem, ...]) -> tuple[str, str]:
        status_count = sum(item.item_kind == "status_notice" for item in items)
        revision_count = sum(
            item.item_kind == "paper"
            and item.event_kind == "revision_available"
            for item in items
        )
        non_new_paper_count = sum(
            item.item_kind == "paper"
            and item.event_kind != "new_work"
            for item in items
        )
        if status_count == len(items):
            return (
                f"Paper Radar｜研究狀態更新 {status_count} 則",
                "Paper Radar 研究狀態更新",
            )
        if revision_count == len(items):
            return (
                f"Paper Radar｜論文更新 {revision_count} 篇",
                "Paper Radar 論文更新",
            )
        if status_count or non_new_paper_count:
            return (
                f"Paper Radar｜每日更新 {len(items)} 則",
                "Paper Radar 每日更新",
            )
        domain_label = cls._domain_label(items)
        if domain_label is not None:
            return (
                f"Paper Radar｜{domain_label}｜每日新論文 {len(items)} 篇",
                f"Paper Radar {domain_label} 每日新論文",
            )
        return (
            f"Paper Radar｜每日新論文 {len(items)} 篇",
            "Paper Radar 每日新論文",
        )

    @staticmethod
    def render(
        items: tuple[SelectedDigestItem, ...],
        *,
        settings_url: str | None,
        coverage_notes: tuple[str, ...] = (),
    ) -> tuple[str, str, str]:
        subject, heading = DigestPreviewTemplate._heading(items)
        text_lines = [heading, ""]
        html_items: list[str] = []
        labels = {"correction": "更正通知", "retraction": "撤稿通知"}
        paper_labels = {
            "revision_available": ("更新", "論文更新"),
            "newly_accessible": ("新可讀", "新增可取得"),
            "late_discovery": ("較早收錄", "較早發現"),
        }

        for index, item in enumerate(items, start=1):
            if item.item_kind == "status_notice":
                label = labels[item.event_kind]
                text_lines.extend(
                    [
                        f"{index}. [{label}] {item.title}",
                        *[f"- {line}" for line in item.plain_language],
                    ]
                )
                title = html.escape(item.title)
                label_html = html.escape(label)
                meta_html = f"<p>類型：{label_html}</p>"
            else:
                domains = "、".join(item.domains)
                paper_label = paper_labels.get(item.event_kind)
                prefix = "" if paper_label is None else f"[{paper_label[0]}] "
                meta_lines = [f"領域：{domains}"]
                meta_html_parts = [
                    f"<p>領域：{html.escape(domains)}</p>",
                ]
                if paper_label is not None:
                    meta_lines.insert(0, f"類型：{paper_label[1]}")
                    meta_html_parts.insert(
                        0,
                        f"<p>類型：{html.escape(paper_label[1])}</p>",
                    )
                text_lines.extend(
                    [
                        f"{index}. {prefix}{item.title}",
                        *meta_lines,
                        *[f"- {line}" for line in item.plain_language],
                    ]
                )
                title = html.escape(prefix + item.title)
                meta_html = "".join(meta_html_parts)

            if item.source_url is not None:
                text_lines.append(f"來源：{item.source_url}")
            text_lines.append("")

            paragraphs = "".join(
                f"<p>{html.escape(line)}</p>" for line in item.plain_language
            )
            source = ""
            if item.source_url is not None:
                href = html.escape(item.source_url, quote=True)
                source = f'<p><a href="{href}">查看來源</a></p>'
            html_items.append(
                f"<li><h2>{title}</h2>{meta_html}{paragraphs}{source}</li>"
            )

        if coverage_notes:
            text_lines.extend(["資料覆蓋提醒：", *[f"- {note}" for note in coverage_notes], ""])
            coverage_html = "<section><h2>資料覆蓋提醒</h2><ul>" + "".join(
                f"<li>{html.escape(note)}</li>" for note in coverage_notes
            ) + "</ul></section>"
        else:
            coverage_html = ""

        if settings_url is not None:
            text_lines.extend(["設定：" + settings_url, ""])
            settings = html.escape(settings_url, quote=True)
            settings_html = f'<p><a href="{settings}">調整 Paper Radar 設定</a></p>'
        else:
            settings_html = ""

        text_body = "\n".join(text_lines).rstrip() + "\n"
        html_body = (
            f"<!doctype html><html><body><h1>{html.escape(heading)}</h1>"
            f"<ol>{''.join(html_items)}</ol>{coverage_html}{settings_html}</body></html>"
        )
        return subject, text_body, html_body
