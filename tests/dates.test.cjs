const test = require('node:test');
const assert = require('node:assert/strict');
const dates = require('../static/js/jalali-date.js');

test('Persian month boundaries round trip', () => {
    for (const value of [[1405,6,31],[1405,7,1],[1406,1,1],[1403,12,30]]) {
        const iso = dates.toGregorian(...value);
        assert.ok(iso);
        assert.deepEqual(dates.fromGregorian(iso),value);
    }
});
test('Persian and Arabic digits parse with two supported date formats', () => {
    const expected = dates.toGregorian(1405,7,1);
    assert.equal(dates.parse('۱۴۰۵/۰۷/۰۱'),expected);
    assert.equal(dates.parse('١٤٠٥/٠٧/٠١'),expected);
    assert.equal(dates.parse('۱ مهر ۱۴۰۵'),expected);
});
test('Invalid dates stay invalid rather than spilling into another month', () => {
    assert.equal(dates.toGregorian(1404,12,30),null);
    assert.equal(dates.toGregorian(1405,7,31),null);
    assert.equal(dates.parse('not a date'),null);
});
