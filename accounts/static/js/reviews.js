/* Customer Reviews: reveal-on-scroll + accessible lightbox. No dependencies. */
(function () {
    'use strict';

    var section = document.getElementById('reviews');
    var box = document.getElementById('rv-lightbox');
    if (!section || !box) return;

    // belt and braces: make sure the dialog is a direct child of <body>
    if (box.parentNode !== document.body) document.body.appendChild(box);

    var cards = Array.prototype.slice.call(section.querySelectorAll('.reviews__card'));
    var img = box.querySelector('.rv-lightbox__img');
    var count = box.querySelector('.rv-lightbox__count');
    var closeBtn = box.querySelector('.rv-lightbox__close');
    var prevBtn = box.querySelector('.rv-lightbox__prev');
    var nextBtn = box.querySelector('.rv-lightbox__next');
    var current = 0;
    var opener = null;

    /* ---------- spotlight carousel ---------- */
    var track = document.getElementById('reviews-track');
    var slides = Array.prototype.slice.call(track.querySelectorAll('.reviews__item'));
    var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    var active = 0, ticking = false;

    // arrows + dots (only worth showing with more than one review)
    var stage = track.parentNode, dots = [], prev, next;
    if (slides.length > 1) {
        prev = document.createElement('button');
        prev.type = 'button'; prev.className = 'reviews__arrow reviews__arrow--prev';
        prev.setAttribute('aria-label', 'Previous / السابق');
        prev.innerHTML = '<i class="ri-arrow-left-s-line" aria-hidden="true"></i>';
        next = document.createElement('button');
        next.type = 'button'; next.className = 'reviews__arrow reviews__arrow--next';
        next.setAttribute('aria-label', 'Next / التالي');
        next.innerHTML = '<i class="ri-arrow-right-s-line" aria-hidden="true"></i>';
        stage.appendChild(prev); stage.appendChild(next);

        var dotBox = document.createElement('div');
        dotBox.className = 'reviews__dots';
        slides.forEach(function (_, i) {
            var d = document.createElement('button');
            d.type = 'button'; d.className = 'reviews__dot';
            d.setAttribute('aria-label', 'Review ' + (i + 1));
            d.addEventListener('click', function () { goTo(i); });
            dotBox.appendChild(d); dots.push(d);
        });
        stage.appendChild(dotBox);
        prev.addEventListener('click', function () { goTo(active - 1); });
        next.addEventListener('click', function () { goTo(active + 1); });
    }

    function goTo(i) {
        i = Math.max(0, Math.min(slides.length - 1, i));
        var el = slides[i];
        track.scrollTo({
            left: el.offsetLeft + el.offsetWidth / 2 - track.clientWidth / 2,
            behavior: reduce ? 'auto' : 'smooth'
        });
    }

    function update() {
        ticking = false;
        var mid = track.scrollLeft + track.clientWidth / 2;
        var best = 0, bestD = Infinity;
        slides.forEach(function (el, i) {
            var c = el.offsetLeft + el.offsetWidth / 2;
            var dist = (c - mid) / (track.clientWidth * 0.5);   // -1..1 across half the track
            var a = Math.min(1, Math.abs(dist));
            var card = el.firstElementChild;
            card.style.setProperty('--rv-p', (1 - a).toFixed(3));
            card.style.setProperty('--rv-s', (1 - 0.2 * a).toFixed(3));
            card.style.setProperty('--rv-o', (1 - 0.35 * a).toFixed(3));
            card.style.setProperty('--rv-ry', reduce ? '0deg' : (-Math.sign(dist) * 32 * a).toFixed(1) + 'deg');
            if (Math.abs(c - mid) < bestD) { bestD = Math.abs(c - mid); best = i; }
        });
        active = best;
        dots.forEach(function (d, i) { d.classList.toggle('is-active', i === best); });
        if (prev) { prev.disabled = best === 0; next.disabled = best === slides.length - 1; }
    }

    track.addEventListener('scroll', function () {
        if (!ticking) { ticking = true; requestAnimationFrame(update); }
    }, { passive: true });
    window.addEventListener('resize', function () { goTo(active); update(); });
    // images change widths as they load: re-centre once they are in
    Array.prototype.forEach.call(track.querySelectorAll('img'), function (im) {
        if (!im.complete) im.addEventListener('load', function () { goTo(active); update(); }, { once: true });
    });
    goTo(0); update();

    /* ---------- lightbox ---------- */
    function srcOf(i) {
        var el = cards[i].querySelector('img');
        return { src: el.currentSrc || el.src, alt: el.alt };
    }

    function show(i) {
        current = (i + cards.length) % cards.length;
        var data = srcOf(current);
        img.src = data.src;
        img.alt = data.alt;
        count.textContent = (current + 1) + ' / ' + cards.length;
        // warm the neighbours so next/prev feel instant
        [current + 1, current - 1].forEach(function (n) {
            if (cards.length > 1) new Image().src = srcOf((n + cards.length) % cards.length).src;
        });
    }

    function open(i) {
        opener = document.activeElement;
        box.classList.toggle('is-single', cards.length < 2);
        show(i);
        box.hidden = false;
        document.body.classList.add('rv-lock');
        closeBtn.focus();
    }

    function close() {
        box.hidden = true;
        img.removeAttribute('src');
        document.body.classList.remove('rv-lock');
        if (opener && opener.focus) opener.focus();
    }

    cards.forEach(function (card, i) {
        card.addEventListener('click', function () {
            if (i === active) open(i); else goTo(i);
        });
    });

    closeBtn.addEventListener('click', close);
    prevBtn.addEventListener('click', function () { show(current - 1); });
    nextBtn.addEventListener('click', function () { show(current + 1); });

    // click on the dark backdrop closes; clicking the image itself does not
    box.addEventListener('click', function (e) {
        if (e.target === box || e.target.classList.contains('rv-lightbox__figure')) close();
    });

    document.addEventListener('keydown', function (e) {
        if (box.hidden) return;
        if (e.key === 'Escape') close();
        else if (e.key === 'ArrowLeft') show(current - 1);
        else if (e.key === 'ArrowRight') show(current + 1);
        else if (e.key === 'Tab') {            // keep focus inside the dialog
            var f = Array.prototype.filter.call(
                box.querySelectorAll('button'),
                function (b) { return b.offsetParent !== null; }
            );
            if (!f.length) return;
            var first = f[0], last = f[f.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
    });

    // swipe left/right on touch screens
    var startX = null;
    box.addEventListener('touchstart', function (e) { startX = e.touches[0].clientX; }, { passive: true });
    box.addEventListener('touchend', function (e) {
        if (startX === null || cards.length < 2) return;
        var dx = e.changedTouches[0].clientX - startX;
        startX = null;
        if (Math.abs(dx) > 50) show(current + (dx < 0 ? 1 : -1));
    }, { passive: true });
})();
