"""Execute the shipped script against DOM fixtures; not a browser qualification."""
import json
import shutil
import subprocess

import pytest

from ihav_competitor_search.render import REPORT_SCRIPT


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed for report script unit checks")
def test_report_numeric_sort_filter_and_recorded_rank_preservation():
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const records = [
  ['negative low', '-1.2'], ['negative high', '-1.1'],
  ['decimal high', '2.9'], ['decimal low', '2.12'],
  ['exponent', '1e+20'], ['ordinary', '9000000000.0'],
  ['huge low', '1000000000000000000000000000000000000000'],
  ['huge high', '1000000000000000000000000000000000000001'], ['missing', '']
];
const rows = records.map(([name, number], index) => ({
  name, textContent: name + ' ' + number, hidden: false, recordedRank: index,
  cells: [{dataset: {sort: number, type: 'number'}}]
}));
const parent = {attrs: {}, getAttribute(k) {return this.attrs[k]},
  setAttribute(k,v) {this.attrs[k]=v}, removeAttribute(k) {delete this.attrs[k]}};
const button = {dataset: {column: '0'}, parentElement: parent,
  addEventListener(_,fn) {this.click=fn}};
let displayed = [];
const body = {rows, appendChild(row) {displayed = displayed.filter(r=>r!==row);displayed.push(row)}};
const table = {tBodies: [body], querySelectorAll(selector) {return selector==='th button' ? [button] : [parent]}};
const filter = {addEventListener(_,fn) {this.input=fn}};
const visible = {};
const document = {getElementById(id) {return {results: table, filter, visible}[id]}};
vm.runInNewContext(SCRIPT, {document});
button.click();
assert.deepEqual(displayed.map(r=>r.name), ['negative low','negative high','decimal low','decimal high','ordinary','exponent','huge low','huge high','missing']);
assert.equal(parent.attrs['aria-sort'], 'ascending');
button.click();
assert.deepEqual(displayed.map(r=>r.name), ['huge high','huge low','exponent','ordinary','decimal high','decimal low','negative high','negative low','missing']);
filter.input({target:{value:'decimal'}});
assert.equal(visible.textContent, 2);
assert.equal(rows.filter(r=>!r.hidden).length, 2);
for (const row of rows) assert.equal(row.recordedRank, records.findIndex(([name])=>name===row.name));
"""
    source = "const SCRIPT = " + json.dumps(REPORT_SCRIPT) + ";\n" + harness
    result = subprocess.run([shutil.which("node"), "-e", source], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
