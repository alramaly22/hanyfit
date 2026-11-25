/* Background particles.
 *
 * - Skipped on phones and for visitors who asked for reduced motion
 *   (it is decoration, and it keeps the CPU busy).
 * - The library is only downloaded when it will actually be used, and only
 *   after the page has finished loading, so it never competes with content.
 * - 100 -> 35 particles, and the frame rate is capped.
 */
(function () {
    // On some pages the #tsparticles div does not exist in the HTML; the
    // library creates it by itself, exactly as before.
    var host = document.getElementById("tsparticles");

    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var smallScreen = window.matchMedia("(max-width: 767px)").matches;
    if (reduceMotion || smallScreen) {
        if (host) host.style.display = "none";
        return;
    }

    function start() {
        var s = document.createElement("script");
        s.src = "https://cdn.jsdelivr.net/npm/tsparticles@2/tsparticles.bundle.min.js";
        s.async = true;
        s.onload = function () {
            if (!window.tsParticles) return;
            window.tsParticles.load("tsparticles", {
                fpsLimit: 40,
                particles: {
                    number: { value: 35 },
                    color: { value: "#c75b16" },
                    move: { enable: true, speed: 1 },
                    size: { value: 2 }
                }
            });
        };
        document.head.appendChild(s);
    }

    function afterLoad() {
        if ("requestIdleCallback" in window) {
            window.requestIdleCallback(start, { timeout: 2000 });
        } else {
            setTimeout(start, 1200);
        }
    }

    if (document.readyState === "complete") afterLoad();
    else window.addEventListener("load", afterLoad);
})();
