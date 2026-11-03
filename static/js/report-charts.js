(() => {
    'use strict';
    const initialized = new WeakSet();
    const pieBindings = new WeakMap();
    const NS = 'http://www.w3.org/2000/svg';
    const numbers = new Intl.NumberFormat('fa-IR', {maximumFractionDigits: 2});
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const mediaColors = Array.from({length: 8}, (_, index) => `var(--chart-media-${index + 1})`);
    let currentDialog, cleanup;
    let sequence = 0;
    const animations = new Set();

    function svgNode(tag, attributes = {}, text) {
        const node = document.createElementNS(NS, tag);
        for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function animate(node, frames, options = {}) {
        if (reducedMotion.matches || !node.animate) return;
        const animation = node.animate(frames, {duration: 550, easing: 'cubic-bezier(.22,1,.36,1)', ...options});
        animations.add(animation);
        animation.finished.then(() => animations.delete(animation), () => animations.delete(animation));
    }

    function popover(parent) {
        const tip = element('div', 'chart-popover');
        tip.id = `chart-popover-${++sequence}`;
        tip.setAttribute('role', 'tooltip');
        tip.hidden = true;
        parent.append(tip);
        let anchor, pointer;
        function position(event) {
            if (!anchor || tip.hidden) return;
            if (event) pointer = {clientX: event.clientX, clientY: event.clientY};
            const bounds = anchor.getBoundingClientRect();
            const x = pointer?.clientX ?? (bounds.left + bounds.width / 2);
            const y = pointer?.clientY ?? (bounds.top + bounds.height / 2);
            const width = tip.offsetWidth, height = tip.offsetHeight;
            const left = Math.max(10, Math.min(x - width / 2, window.innerWidth - width - 10));
            const above = y - height - 16;
            const top = Math.max(10, Math.min(above >= 10 ? above : y + 20, window.innerHeight - height - 10));
            tip.style.left = `${left}px`;
            tip.style.top = `${top}px`;
        }
        return {
            show(source, title, rows, footer, event) {
                const entering = tip.hidden;
                if (anchor !== source) anchor?.removeAttribute('aria-describedby');
                anchor = source;
                pointer = null;
                tip.replaceChildren(element('strong', 'chart-popover-title', title));
                for (const row of rows) {
                    const line = element('div', 'chart-popover-row');
                    const name = element('span', 'chart-popover-name');
                    if (row.color) {
                        const dot = svgNode('svg', {viewBox: '0 0 12 12', class: 'chart-swatch', 'aria-hidden': 'true'});
                        dot.append(svgNode('rect', {x: 0, y: 0, width: 12, height: 12, rx: 4, fill: row.color}));
                        name.append(dot);
                    }
                    name.append(document.createTextNode(row.name));
                    line.append(name, element('b', '', row.value));
                    tip.append(line);
                }
                if (footer) tip.append(element('small', 'chart-popover-footer', footer));
                anchor.setAttribute('aria-describedby', tip.id);
                tip.hidden = false;
                position(event);
                if (entering) animate(tip, [{opacity: 0, transform: 'translateY(5px) scale(.97)'},
                    {opacity: 1, transform: 'translateY(0) scale(1)'}], {duration: 180});
            },
            position,
            track() {
                if (!anchor || tip.hidden) return;
                const bounds = anchor.getBoundingClientRect();
                const inView = anchor.isConnected && bounds.bottom > 0 && bounds.top < window.innerHeight
                    && bounds.right > 0 && bounds.left < window.innerWidth;
                const underPointer = pointer && pointer.clientX >= bounds.left && pointer.clientX <= bounds.right
                    && pointer.clientY >= bounds.top && pointer.clientY <= bounds.bottom;
                if (inView && (document.activeElement === anchor || underPointer)) position();
                else this.hide();
            },
            hide() {
                tip.hidden = true;
                anchor?.removeAttribute('aria-describedby');
                anchor = null;
                pointer = null;
            },
            destroy() { tip.remove(); }
        };
    }

    function donutArc(start, end, full) {
        const outer = 134, inner = 94;
        const point = (radius, angle) => [150 + radius * Math.cos(angle), 150 + radius * Math.sin(angle)].join(' ');
        if (full) return `M150 16 A134 134 0 1 1 150 284 A134 134 0 1 1 150 16 Z M150 56 A94 94 0 1 0 150 244 A94 94 0 1 0 150 56 Z`;
        const gap = Math.min(0.035, (end - start) * 0.16);
        const a = start + gap / 2, b = end - gap / 2;
        const corner = Math.min(5, (b - a) * inner / 4);
        const co = corner / outer, ci = corner / inner;
        return `M${point(outer, a + co)} A${outer} ${outer} 0 ${b - a - 2 * co > Math.PI ? 1 : 0} 1 ${point(outer, b - co)} `
            + `Q${point(outer, b)} ${point(outer - corner, b)} L${point(inner + corner, b)} `
            + `Q${point(inner, b)} ${point(inner, b - ci)} A${inner} ${inner} 0 ${b - a - 2 * ci > Math.PI ? 1 : 0} 0 ${point(inner, a + ci)} `
            + `Q${point(inner, a)} ${point(inner + corner, a)} L${point(outer - corner, a)} `
            + `Q${point(outer, a)} ${point(outer, a + co)} Z`;
    }

    function pie(container, items, label, tip, onSelect) {
        pieBindings.get(container)?.abort();
        const binding = new AbortController();
        pieBindings.set(container, binding);
        const on = (node, event, action) => node.addEventListener(event, action, {signal: binding.signal});
        container.replaceChildren();
        const total = items.reduce((sum, item) => sum + item.units, 0);
        if (!total) {
            container.append(element('p', 'chart-empty', 'در این گروه برای سال انتخاب‌شده حجمی ثبت نشده است.'));
            return {highlight() {}, reset() { tip.hide(); }};
        }
        const svg = svgNode('svg', {viewBox: '0 0 300 300', role: 'group', 'aria-label': label});
        svg.append(svgNode('circle', {cx: 150, cy: 150, r: 114, class: 'chart-donut-track'}));
        const center = svgNode('text', {x: 150, y: 147, class: 'chart-pie-total', 'text-anchor': 'middle', 'aria-hidden': 'true'}, numbers.format(total / 100));
        const caption = svgNode('text', {x: 150, y: 176, class: 'chart-pie-caption', 'text-anchor': 'middle', 'aria-hidden': 'true'}, 'کادر کل');
        const shapes = new Map();
        let angle = -Math.PI / 2;
        const reset = () => {
            container.classList.remove('is-interacting');
            for (const shape of shapes.values()) shape.classList.remove('is-highlighted');
            center.textContent = numbers.format(total / 100);
            caption.textContent = 'کادر کل';
            tip.hide();
        };
        const highlight = (index, source, event) => {
            const item = items[index];
            if (!item) return;
            container.classList.add('is-interacting');
            for (const [key, shape] of shapes) shape.classList.toggle('is-highlighted', key === index);
            center.textContent = `${numbers.format(Number(item.percent))}٪`;
            caption.textContent = onSelect ? 'سهم از کل سال' : 'سهم از گروه';
            tip.show(source, item.name, [
                {name: 'حجم تخصیص', value: `${numbers.format(item.units / 100)} کادر`, color: item.color},
                {name: onSelect ? 'سهم از کل سال' : 'سهم از گروه', value: `${numbers.format(Number(item.percent))}٪`}
            ], onSelect ? 'برای مشاهده رسانه‌های این گروه کلیک کنید' : null, event);
        };
        items.forEach((item, index) => {
            if (!item.units) return;
            const end = angle + item.units / total * Math.PI * 2;
            const description = `${item.name}: ${numbers.format(Number(item.percent))}٪ · ${numbers.format(item.units / 100)} کادر`;
            const shape = svgNode('path', {d: donutArc(angle, end, item.units === total), fill: item.color,
                'fill-rule': 'evenodd', class: 'chart-pie-slice', tabindex: 0,
                role: onSelect ? 'button' : 'img', 'aria-label': description});
            shape.dataset.pieIndex = index;
            if (onSelect) {
                shape.setAttribute('aria-haspopup', 'dialog');
                shape.dataset.chartSlice = item.key;
                on(shape, 'click', () => { reset(); onSelect(item.key, shape); });
            }
            on(shape, 'pointerenter', event => { if (event.pointerType !== 'touch') highlight(index, shape, event); });
            on(shape, 'pointermove', event => { if (event.pointerType !== 'touch') tip.position(event); });
            on(shape, 'pointerdown', event => { if (!onSelect && event.pointerType === 'touch') highlight(index, shape); });
            on(shape, 'focus', () => highlight(index, shape));
            on(shape, 'blur', reset);
            on(shape, 'keydown', event => {
                if (event.key === 'Escape') reset();
                if (onSelect && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault();
                    reset();
                    onSelect(item.key, shape);
                }
            });
            shapes.set(index, shape);
            svg.append(shape);
            angle = end;
        });
        svg.append(center, caption);
        on(container, 'pointerleave', event => { if (event.pointerType !== 'touch') reset(); });
        container.append(svg);
        container.hidden = false;
        animate(svg, [{opacity: 0, transform: 'scale(.94)'}, {opacity: 1, transform: 'scale(1)'}], {duration: 650});
        return {highlight, reset};
    }

    // A monotone curve rounds joins without introducing peaks or negative volumes.
    function smoothPath(points) {
        const slopes = points.slice(1).map((point, index) => (point.y - points[index].y) / (point.x - points[index].x));
        const tangents = points.map((_, index) => {
            if (!index) return slopes[0];
            if (index === points.length - 1) return slopes.at(-1);
            const before = slopes[index - 1], after = slopes[index];
            return before * after <= 0 ? 0 : 2 * before * after / (before + after);
        });
        return points.reduce((path, point, index) => {
            if (!index) return `M${point.x} ${point.y}`;
            const previous = points[index - 1], third = (point.x - previous.x) / 3;
            return path + ` C${previous.x + third} ${previous.y + tangents[index - 1] * third} `
                + `${point.x - third} ${point.y - tangents[index] * third} ${point.x} ${point.y}`;
        }, '');
    }

    function monthlyChart(container, data, tip) {
        const width = 680, height = 292, left = 64, right = 650, top = 24, bottom = 236;
        const maximum = Math.max(...data.months.flatMap(month => [month.local, month.national])) / 100;
        const raw = Math.max(maximum, 0.01) / 4;
        const magnitude = 10 ** Math.floor(Math.log10(raw));
        const normalized = raw / magnitude;
        const step = Math.max(0.01, (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude);
        const ceiling = Math.max(step, Math.ceil(maximum / step) * step);
        const slot = (right - left) / 12;
        const x = index => left + slot * (index + 0.5);
        const y = units => bottom - (units / 100) / ceiling * (bottom - top);
        const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, role: 'group',
            'aria-label': `روند ماهانه حجم تخصیص در سال ${data.year}؛ هر ماه را برای جزئیات انتخاب کنید.`});
        const definitions = svgNode('defs');
        const plot = svgNode('rect', {x: left, y: top, width: right - left, height: bottom - top, rx: 12, class: 'chart-plot-background'});
        svg.append(definitions, plot);
        for (let tick = 0; tick <= Math.round(ceiling / step); tick++) {
            const value = tick * step, position = bottom - value / ceiling * (bottom - top);
            svg.append(svgNode('line', {x1: left, x2: right, y1: position, y2: position, class: 'chart-grid-line'}));
            svg.append(svgNode('text', {x: left - 12, y: position + 4, 'text-anchor': 'end', class: 'chart-axis-label'}, numbers.format(value)));
        }
        data.months.forEach((month, index) => svg.append(svgNode('text', {
            x: x(index), y: bottom + 29, 'text-anchor': 'middle', class: 'chart-month-label', 'data-month-label': index
        }, month.name)));
        const lines = svgNode('g', {class: 'chart-mode-lines'});
        const bars = svgNode('g', {class: 'chart-mode-bars'});
        const pointNodes = [], barNodes = [];
        for (const [groupIndex, group] of data.groups.entries()) {
            const color = `var(--chart-${group.key})`;
            const gradientId = `chart-area-${group.key}-${++sequence}`;
            const gradient = svgNode('linearGradient', {id: gradientId, x1: 0, x2: 0, y1: 0, y2: 1});
            gradient.append(svgNode('stop', {offset: '0%', 'stop-color': color, 'stop-opacity': '.18'}),
                svgNode('stop', {offset: '100%', 'stop-color': color, 'stop-opacity': '0'}));
            definitions.append(gradient);
            const points = data.months.map((month, index) => ({x: x(index), y: y(month[group.key])}));
            const path = smoothPath(points);
            lines.append(svgNode('path', {d: `${path} L${x(11)} ${bottom} L${x(0)} ${bottom} Z`, fill: `url(#${gradientId})`, class: 'chart-area'}));
            lines.append(svgNode('path', {d: path, class: `chart-line chart-line-${group.key}`}));
            points.forEach((point, index) => {
                const circle = svgNode('circle', {cx: point.x, cy: point.y, r: 4,
                    class: `chart-point chart-point-${group.key}`, 'data-point-month': index});
                lines.append(circle);
                pointNodes.push(circle);
                const barWidth = slot * 0.27;
                const bar = svgNode('rect', {x: point.x + (groupIndex ? 2 : -barWidth - 2), y: point.y,
                    width: barWidth, height: bottom - point.y, rx: 5,
                    class: `chart-bar chart-bar-${group.key}`, 'data-bar-month': index});
                bars.append(bar);
                barNodes.push(bar);
            });
        }
        const guide = svgNode('line', {x1: 0, x2: 0, y1: top, y2: bottom, class: 'chart-hover-guide', visibility: 'hidden'});
        svg.append(lines, bars, guide);
        const reset = () => {
            guide.setAttribute('visibility', 'hidden');
            container.classList.remove('is-interacting');
            svg.querySelectorAll('.is-highlighted').forEach(node => node.classList.remove('is-highlighted'));
            tip.hide();
        };
        data.months.forEach((month, index) => {
            const hit = svgNode('rect', {x: left + index * slot, y: top, width: slot, height: bottom - top,
                fill: 'transparent', class: 'chart-month-hit', tabindex: 0, role: 'button', 'data-chart-month': index,
                'aria-label': `${month.name}؛ محلی ${numbers.format(month.local / 100)} و سراسری ${numbers.format(month.national / 100)} کادر`});
            function activate(event) {
                guide.setAttribute('x1', x(index));
                guide.setAttribute('x2', x(index));
                guide.setAttribute('visibility', 'visible');
                container.classList.add('is-interacting');
                for (const node of pointNodes) node.classList.toggle('is-highlighted', Number(node.dataset.pointMonth) === index);
                for (const node of barNodes) node.classList.toggle('is-highlighted', Number(node.dataset.barMonth) === index);
                svg.querySelectorAll('[data-month-label]').forEach(node => node.classList.toggle('is-highlighted', Number(node.dataset.monthLabel) === index));
                tip.show(hit, `${month.name} ${numbers.format(data.year)}`, data.groups.map(group => ({
                    name: group.label, value: `${numbers.format(month[group.key] / 100)} کادر`, color: `var(--chart-${group.key})`
                })), `مجموع ماه: ${numbers.format((month.local + month.national) / 100)} کادر`, event);
            }
            hit.addEventListener('pointerenter', event => { if (event.pointerType !== 'touch') activate(event); });
            hit.addEventListener('pointermove', event => { if (event.pointerType !== 'touch') tip.position(event); });
            hit.addEventListener('pointerdown', event => { if (event.pointerType === 'touch') activate(); });
            hit.addEventListener('focus', () => activate());
            hit.addEventListener('blur', reset);
            hit.addEventListener('keydown', event => {
                if (event.key === 'Escape') reset();
                if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); }
                if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
                    event.preventDefault();
                    const next = index + (event.key === 'ArrowRight' ? 1 : -1);
                    svg.querySelector(`[data-chart-month="${next}"]`)?.focus();
                }
            });
            svg.append(hit);
        });
        container.addEventListener('pointerleave', event => { if (event.pointerType !== 'touch') reset(); });
        container.addEventListener('scroll', reset, {passive: true});
        container.replaceChildren(svg);
        container.hidden = false;
        container.dataset.mode = 'line';
        animate(lines, [{opacity: 0, transform: 'translateY(8px)'}, {opacity: 1, transform: 'translateY(0)'}], {duration: 750});
        return mode => {
            reset();
            container.dataset.mode = mode;
            const group = mode === 'bar' ? bars : lines;
            animate(group, [{opacity: 0, transform: 'translateY(8px)'}, {opacity: 1, transform: 'translateY(0)'}], {duration: 420});
        };
    }

    function initialize() {
        const root = document.querySelector('[data-report-charts]');
        if (!root || initialized.has(root)) return;
        initialized.add(root);
        const signal = new AbortController();
        const data = JSON.parse(root.querySelector('[data-report-chart-data]').textContent);
        const dialog = root.querySelector('[data-chart-dialog]');
        const summaryTip = popover(document.body), detailTip = popover(dialog);
        const tips = [summaryTip, detailTip];
        const detailPie = root.querySelector('[data-detail-pie]');
        const detailLegend = root.querySelector('[data-detail-legend]');
        let trigger, summaryPie;
        function openGroup(key, source) {
            const group = data.groups.find(item => item.key === key);
            if (!group) return;
            tips.forEach(tip => tip.hide());
            summaryPie?.reset();
            trigger = source;
            root.querySelector('#chart-detail-title').textContent = `سهم ${group.label}`;
            root.querySelector('#chart-detail-description').textContent =
                `سال ${numbers.format(data.year)} · ${numbers.format(group.units / 100)} کادر · درصد هر رسانه از کل حجم همین گروه`;
            const media = group.media.map((item, index) => ({...item, color: mediaColors[index % mediaColors.length]}));
            const detailChart = pie(detailPie, media, `درصد سهم هر رسانه در ${group.label}`, detailTip);
            detailLegend.replaceChildren();
            media.forEach((item, index) => {
                const row = element('div', 'chart-media-row');
                const swatch = svgNode('svg', {viewBox: '0 0 14 14', class: 'chart-swatch', 'aria-hidden': 'true'});
                swatch.append(svgNode('rect', {width: 14, height: 14, rx: 4, fill: item.color}));
                const link = element('a', '', item.name);
                link.href = item.url;
                const description = element('span');
                description.append(link, element('small', '', `${numbers.format(item.units / 100)} کادر`));
                row.append(swatch, description, element('strong', '', `${numbers.format(Number(item.percent))}٪`));
                row.addEventListener('pointerenter', event => { if (event.pointerType !== 'touch') detailChart.highlight(index, row, event); });
                row.addEventListener('pointermove', event => detailTip.position(event));
                row.addEventListener('pointerleave', () => detailChart.reset());
                link.addEventListener('focus', () => detailChart.highlight(index, link));
                link.addEventListener('blur', () => detailChart.reset());
                detailLegend.append(row);
            });
            currentDialog = dialog;
            dialog.showModal();
            animate(dialog, [{opacity: 0}, {opacity: 1}], {duration: 250});
            animate(root.querySelector('.chart-detail-layout'), [{opacity: 0, transform: 'translateY(10px)'},
                {opacity: 1, transform: 'translateY(0)'}], {duration: 450});
        }
        root.querySelector('[data-chart-close]').addEventListener('click', () => dialog.close());
        dialog.addEventListener('click', event => {
            if (event.target !== dialog) return;
            const bounds = dialog.getBoundingClientRect();
            if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
        });
        dialog.addEventListener('scroll', () => detailTip.hide(), {passive: true});
        dialog.addEventListener('close', () => {
            detailTip.hide();
            if (currentDialog === dialog) currentDialog = null;
            if (trigger?.isConnected) trigger.focus({preventScroll: true});
        });
        root.querySelectorAll('[data-chart-category]').forEach((button, index) => {
            button.disabled = false;
            button.addEventListener('click', () => openGroup(button.dataset.chartCategory, button));
            button.addEventListener('pointerenter', event => { if (event.pointerType !== 'touch') summaryPie?.highlight(index, button, event); });
            button.addEventListener('pointermove', event => summaryTip.position(event));
            button.addEventListener('pointerleave', () => summaryPie?.reset());
            button.addEventListener('focus', () => summaryPie?.highlight(index, button));
            button.addEventListener('blur', () => summaryPie?.reset());
        });
        const summary = root.querySelector('[data-summary-pie]');
        if (summary) summaryPie = pie(summary, data.groups.map(group => ({...group, name: group.label,
            color: `var(--chart-${group.key})`})), 'سهم حجم سالانه رسانه‌های محلی و سراسری', summaryTip, openGroup);
        const monthly = root.querySelector('[data-line-chart]');
        if (monthly) {
            const changeMode = monthlyChart(monthly, data, summaryTip);
            root.querySelectorAll('[data-chart-mode]').forEach(button => {
                button.disabled = false;
                button.addEventListener('click', () => {
                    if (button.getAttribute('aria-pressed') === 'true') return;
                    root.querySelectorAll('[data-chart-mode]').forEach(option => option.setAttribute('aria-pressed', String(option === button)));
                    changeMode(button.dataset.chartMode);
                });
            });
        }
        document.addEventListener('pointerdown', event => {
            if (!event.target.closest('.chart-pie, .chart-group-button, .chart-line-scroll, .chart-media-row')) tips.forEach(tip => tip.hide());
        }, {signal: signal.signal});
        window.addEventListener('scroll', () => tips.forEach(tip => tip.track()), {signal: signal.signal, passive: true});
        window.addEventListener('resize', () => tips.forEach(tip => tip.track()), {signal: signal.signal});
        cleanup = () => {
            signal.abort();
            tips.forEach(tip => tip.destroy());
            animations.forEach(animation => animation.cancel());
            animations.clear();
        };
    }

    document.addEventListener('sobhe:before-render', () => {
        if (currentDialog?.open) currentDialog.close();
        currentDialog = null;
        cleanup?.();
        cleanup = null;
    });
    document.addEventListener('sobhe:page-render', initialize);
    initialize();
})();
