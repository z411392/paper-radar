# 工程規則索引

本庫依使用者明示要求對齊 Kaledoxa `6862a93f1a1cc133a5dedfa25ec414b460f3b4ed` 的方法。這是移植後自含規則，不宣稱與來源逐字相同；僅移除 Kaledoxa 私有產品詞、歷史操作／例外與既有 session identities，保留權威、分工、驗收、文件及 Git 追溯契約。

上游核對來源：[共用入口](https://github.com/z411392/kaledoxa/blob/6862a93f1a1cc133a5dedfa25ec414b460f3b4ed/CLAUDE.md)、[Rule 15](https://github.com/z411392/kaledoxa/blob/6862a93f1a1cc133a5dedfa25ec414b460f3b4ed/.claude/rules/15-execution-strategy.md)、[Rule 16](https://github.com/z411392/kaledoxa/blob/6862a93f1a1cc133a5dedfa25ec414b460f3b4ed/.claude/rules/16-github-human-readable-records.md)、[Rule 70](https://github.com/z411392/kaledoxa/blob/6862a93f1a1cc133a5dedfa25ec414b460f3b4ed/.claude/rules/70-testing.md)、[Rule 80](https://github.com/z411392/kaledoxa/blob/6862a93f1a1cc133a5dedfa25ec414b460f3b4ed/.claude/rules/80-documentation.md)、[Rule 90](https://github.com/z411392/kaledoxa/blob/6862a93f1a1cc133a5dedfa25ec414b460f3b4ed/.claude/rules/90-operations.md)。來源更新不自動覆蓋本庫；Commander 先比較直接變更，再依使用者授權同步方法，不繼承 sibling 的產品 scope 或進度。

- [05-methodology.md](05-methodology.md)
- [10-architecture.md](10-architecture.md)
- [15-execution-strategy.md](15-execution-strategy.md)
- [16-github-human-readable-records.md](16-github-human-readable-records.md)
- [20-code-style.md](20-code-style.md)
- [30-application-domain.md](30-application-domain.md)
- [40-ports-adapters-di.md](40-ports-adapters-di.md)
- [50-dtos-errors-http.md](50-dtos-errors-http.md)
- [60-configuration-data.md](60-configuration-data.md)
- [70-testing.md](70-testing.md)
- [80-documentation.md](80-documentation.md)
- [90-operations.md](90-operations.md)

當前 bootstrap 是規劃交付，不是 architecture freeze、runtime dispatch 或 independent ACCEPT。個別 Task 仍須依 Rule 15 完成 Ready 證據。
