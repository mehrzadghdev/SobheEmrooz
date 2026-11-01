/* Calendar conversion uses the browser's Persian calendar, with UTC calendar days. */
(function (root) {
    'use strict';
    const months = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
        'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'];
    const calendar = new Intl.DateTimeFormat('en-US-u-ca-persian-nu-latn', {
        timeZone: 'UTC', year: 'numeric', month: 'numeric', day: 'numeric',
    });
    const dayMilliseconds = 86400000;
    function latin(value) {
        return String(value).replace(/[۰-۹]/g, digit => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(digit)))
            .replace(/[٠-٩]/g, digit => String('٠١٢٣٤٥٦٧٨٩'.indexOf(digit)));
    }
    function persian(value) { return String(value).replace(/\d/g, digit => '۰۱۲۳۴۵۶۷۸۹'[Number(digit)]); }
    function parts(date) {
        const values = Object.fromEntries(calendar.formatToParts(date).map(part => [part.type, part.value]));
        return [Number(values.year), Number(values.month), Number(values.day)];
    }
    function gregorianDate(iso) {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
        if (!match) return null;
        const date = new Date(`${iso}T12:00:00Z`);
        return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === iso ? date : null;
    }
    function fromGregorian(iso) {
        const date = gregorianDate(iso);
        return date ? parts(date) : null;
    }
    function toGregorian(year, month, day) {
        if (![year, month, day].every(Number.isInteger) || year < 1 || year > 3000
            || month < 1 || month > 12 || day < 1 || day > (month <= 6 ? 31 : 30)) return null;
        const order = ([y, m, d]) => y * 372 + m * 31 + d;
        const target = order([year, month, day]);
        let low = Math.floor(Date.UTC(year + 621, 0, 1, 12) / dayMilliseconds);
        let high = Math.floor(Date.UTC(year + 622, 11, 31, 12) / dayMilliseconds);
        while (low <= high) {
            const middle = Math.floor((low + high) / 2);
            const date = new Date(middle * dayMilliseconds + dayMilliseconds / 2);
            const comparison = order(parts(date)) - target;
            if (!comparison) return date.toISOString().slice(0, 10);
            if (comparison < 0) low = middle + 1;
            else high = middle - 1;
        }
        return null;
    }
    function parse(value) {
        const normalized = latin(value).replace(/[\u200c\u200e\u200f\u202a-\u202e]/g, ' ')
            .replace(/ي/g, 'ی').replace(/ك/g, 'ک').trim().replace(/\s+/g, ' ');
        let match = /^(\d{1,4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})$/.exec(normalized);
        if (match) return toGregorian(...match.slice(1).map(Number));
        match = /^(\d{1,2}) ([^\d]+) (\d{1,4})$/.exec(normalized);
        return match ? toGregorian(Number(match[3]), months.indexOf(match[2]) + 1, Number(match[1])) : null;
    }
    function format(iso) {
        const value = fromGregorian(iso);
        return value ? `${persian(value[2])} ${months[value[1] - 1]} ${persian(value[0])}` : '';
    }
    function addDays(iso, amount) {
        const date = gregorianDate(iso);
        return date ? new Date(date.getTime() + amount * dayMilliseconds).toISOString().slice(0, 10) : null;
    }
    function today() {
        const values = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
            timeZone: 'Asia/Tehran', year: 'numeric', month: '2-digit', day: '2-digit',
        }).formatToParts(new Date()).map(part => [part.type, part.value]));
        return `${values.year}-${values.month}-${values.day}`;
    }
    const api = {months, latin, persian, fromGregorian, toGregorian, parse, format, addDays, today};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.SobheDates = api;
})(globalThis);
