import subprocess
from pathlib import Path
from textwrap import dedent, indent


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def block(value, spaces):
    return indent(dedent(value).strip() + "\n", " " * spaces)


adapter = Path("src/libs/discovery/adapters/driven/sqlite_harvest_processing_adapter.py")
rules = Path("src/libs/discovery/domain/services/harvest_page_rules.py")
assert git("hash-object", str(adapter)) == "533cb003544e618cf6deb4314516b144a7de0f3a"
assert git("hash-object", str(rules)) == "c1f0a4c340f17dbc3056d9f900d3725a4ef1ecf1"
text = adapter.read_text()


def replace(old, new):
    global text
    assert text.count(old) == 1, old
    text = text.replace(old, new, 1)


replace("        previous = None\n", "        previous = None\n        receipts: list[HarvestPageResult] = []\n")
replace("            if stored_key == key:\n", "            receipts.append(receipt)\n            if stored_key == key:\n")
replace(
    '        else:\n            cursor = HarvestPageRules.decode(unit["cursor_json"])',
    '        else:\n            if state in {"pending", "running", "failed"}:\n'
    '                raise HarvestError("invalid_checkpoint_state")\n'
    '            if (type(coverage.get("format_version")) is not int\n'
    '                    or type(coverage.get("record_count")) is not int\n'
    '                    or type(coverage.get("complete")) is not bool):\n'
    '                raise HarvestError("invalid_checkpoint_state")\n'
    '            cursor = HarvestPageRules.decode(unit["cursor_json"])',
)
start = text.index("        records = tuple(\n")
end = text.index("        return HarvestProcessingSnapshot(\n", start)
replacement = block('''
observations = connection.execute(
    "SELECT id,native_id FROM source_observations WHERE unit_id=? ORDER BY native_id LIMIT 30001",
    (attempt.unit_id,),
).fetchall()
records = tuple(item["native_id"] for item in observations)
available_ids = {item["id"] for item in observations}
if len(records) != next_start or len(set(records)) != len(records):
    raise HarvestError("invalid_checkpoint_observations")
if version > max(1, next_start):
    raise HarvestError("invalid_checkpoint_state")
for receipt in receipts:
    if (receipt.checkpoint_version > version or receipt.next_start > next_start
            or not set(receipt.observation_ids).issubset(available_ids)):
        raise HarvestError("invalid_processing_receipt")
    if receipt.error_code is None:
        if (receipt.total_results != total
                or receipt.next_start != attempt.request.start + len(receipt.observation_ids)):
            raise HarvestError("invalid_processing_receipt")
    elif receipt.next_start != attempt.request.start:
        raise HarvestError("invalid_processing_receipt")
''', 8)
text = text[:start] + replacement + text[end:]
compile(text, str(adapter), "exec")
adapter.write_text(text)
text = rules.read_text()
marker = '            if PrepareHarvestCapture.time(datetime.fromisoformat(result.processed_at)) != result.processed_at:'
assert text.count(marker) == 1
extra = block('''
if result.error_code is None:
    total = result.total_results
    if total is None:
        raise HarvestError("invalid_processing_receipt")
    if result.state == "verified_empty":
        if (total != 0 or result.next_start != 0 or result.observation_ids
                or result.expected_checkpoint_version != 0):
            raise HarvestError("invalid_processing_receipt")
    elif (not result.observation_ids or total <= 0
            or (result.state == "succeeded" and result.next_start != total)
            or (result.state == "partial" and not 0 < result.next_start < total)):
        raise HarvestError("invalid_processing_receipt")
elif ((result.state == "failed" and (
        result.expected_checkpoint_version != 0 or result.next_start != 0
        or result.total_results is not None))
        or (result.state == "partial" and (
            result.expected_checkpoint_version == 0 or result.next_start == 0))):
    raise HarvestError("invalid_processing_receipt")
''', 12)
text = text.replace(marker, extra + marker, 1)
marker = "        return HarvestPageResult(\n"
assert text.count(marker) == 1
text = text.replace(marker, "        cls.checkpoint(version)\n" + marker, 1)
compile(text, str(rules), "exec")
rules.write_text(text)
print("BOUNDED_STATE_REPAIR_COMPILES; NO_TEST_EXPECTATIONS_CHANGED")
