/*=============== SHOW MENU ===============*/
const navMenu = document.getElementById('nav-menu'),
    navToggle = document.getElementById('nav-toggle'),
    navClose = document.getElementById('nav-close')

/*===== MENU SHOW =====*/
/* Validate if constant exists */
if (navToggle) {
    navToggle.addEventListener('click', () => {
        navMenu.classList.add('show-menu')
    })
}

/*===== MENU HIDDEN =====*/
/* Validate if constant exists */
if (navClose) {
    navClose.addEventListener('click', () => {
        navMenu.classList.remove('show-menu')
    })
}
/*=============== REMOVE MENU MOBILE ===============*/
const navLink = document.querySelectorAll('.nav__link')

const linkAction = () => {
    const navMenu = document.getElementById('nav-menu')
        // When we click on each nav__link, we remove the show-menu class
    navMenu.classList.remove('show-menu')
}
navLink.forEach(n => n.addEventListener('click', linkAction))

/*=============== CHANGE BACKGROUND HEADER ===============*/
const blurHeader = () => {
    const header = document.getElementById('header')
        // When the scroll is greater than 50 viewport height, add the scroll-header class to the header tag
    this.scrollY >= 50 ? header.classList.add('blur-header') :
        header.classList.remove('blur-header')
}
window.addEventListener('scroll', blurHeader)



// ==============NUMM==============



let valueDisplays = document.querySelectorAll('.num');
let interval = 2000;
let isScrollingDown = false;

// Function to animate counting
// function animateCounters() {
//     valueDisplays.forEach((valueDisplay) => {
//         let startValue = 0;
//         let endValue = parseInt(valueDisplay.getAttribute("data-val"));
//         let duration = Math.floor(interval / endValue);
//         let counter = setInterval(function () {
//             startValue += 1;
//             valueDisplay.textContent = startValue + "K";
//             if (startValue == endValue) {
//                 clearInterval(counter);
//             }
//         }, duration);
//     });
// }


// function resetCounters() {
//     valueDisplays.forEach((valueDisplay) => {
//         valueDisplay.textContent = "0K";
//     });
// }

// window.addEventListener("scroll", function () {
//     if (window.scrollY > 0 && !isScrollingDown) {
//         resetCounters();
//         isScrollingDown = true;
//     } else if (window.scrollY === 0 && isScrollingDown) {
//         animateCounters();
//         isScrollingDown = false;
//     }
// });

// animateCounters();




var TrandingSlider = new Swiper('.tranding-slider', {
    effect: 'coverflow',
    grabCursor: true,
    centeredSlides: true,
    loop: true,
    slidesPerView: 'auto',
    coverflowEffect: {
        rotate: 0,
        stretch: 0,
        depth: 100,
        modifier: 2.5,
    },
    pagination: {
        el: '.swiper-pagination',
        clickable: true,
    },
    navigation: {
        nextEl: '.swiper-button-next',
        prevEl: '.swiper-button-prev',
    }
});


/*=============== HOME SWIPER ===============*/
let swiperProjects = new Swiper(".projects__container", {
    loop: true,
    spaceBetween: 24,
    navigation: {
        nextEl: ".swiper-button-next",
        prevEl: ".swiper-button-prev",
    },
    pagination: {
        el: ".swiper-pagination",
    },
    breakpoints: {
        1200: {
            slidesPerView: 2,
            spaceBetween: -56,
        },
    },
});
/*=============== SHOW SCROLL UP ===============*/
const scrollUp = () => {
    const scrollUp = document.getElementById('scroll-up')
        // When the scroll is higher than 350 viewport height, add the show-scroll class to the a tag with the scrollup class
    this.scrollY >= 350 ? scrollUp.classList.add('show-scroll') :
        scrollUp.classList.remove('show-scroll')
}
window.addEventListener('scroll', scrollUp)
    // var swiper = new Swiper('.swiper-container', {
    //     effect: 'coverflow',
    //     grabCursor: true,
    //     centeredSlides: true,
    //     slidesPerView: 'auto',
    //     coverflowEffect: {
    //       rotate: 50,
    //       stretch: 0,
    //       depth: 100,
    //       modifier:1,
    //       slideShadows:true,
    //     },
    //     pagination: {
    //       el: '.swiper-pagination',

//     },
//     });
/*=============== ACCORDION ===============*/
const accordionItems = document.querySelectorAll('.accordion__item')

// 1. Selecionar cada item
accordionItems.forEach((item) => {
    const accordionHeader = item.querySelector('.accordion__header')

    // 2. Seleccionar cada click del header
    accordionHeader.addEventListener('click', () => {
        // 7. Crear la variable
        const openItem = document.querySelector('.accordion-open')

        // 5. Llamar a la funcion toggle item
        toggleItem(item)

        // 8. Validar si existe la clase
        if (openItem && openItem !== item) {
            toggleItem(openItem)
        }
    })
})

// 3. Crear una funcion tipo constante
const toggleItem = (item) => {
    // 3.1 Crear la variable
    const accordionContent = item.querySelector('.accordion__content')

    // 6. Si existe otro elemento que contenga la clase accorion-open que remueva su clase
    if (item.classList.contains('accordion-open')) {
        accordionContent.removeAttribute('style')
        item.classList.remove('accordion-open')
    } else {
        // 4. Agregar el height maximo del content
        accordionContent.style.height = accordionContent.scrollHeight + 'px'
        item.classList.add('accordion-open')
    }
}

/*=============== Testimonial ===============*/
let swiperTestimonial = new Swiper('.testimonial__container', {
    loop: true,
    spaceBetween: 24,
    grabCursor: true,
    pagination: {
        el: ".swiper-pagination",
        clickable: true,
    },
    autoplay: {
        delay: 3000, // تحديد مدة زمنية بالميللي ثانية، هنا تم تحديدها لتكون 4 ثواني
    },
    breakpoints: {
        576: {
            slidesPerView: 2,
        },
        768: {
            slidesPerView: 2,
            spaceBetween: 48,
        },
    },
});




let swiperFeatured = new Swiper('.featured__swiper', {
    loop: true,
    spaceBetween: 16,
    grabCursor: true,
    slidesPerView: 'auto',
    centeredSlides: 'auto',

    navigation: {
        nextEl: '.swiper-button-next',
        prevEl: '.swiper-button-prev',
    },
    breakpoints: {
        1150: {
            slidesPerView: 3,
            conteredSlides: false,
        },
    },
});




/*=============== Calc ===============*/

/*=============== Calc ===============*/
const swiperWork = new Swiper('.work__swiper', {
    loop: true,
    spaceBetween: 24,
    slidesPerView: 'auto',
    grabCursor: true,
    speed: 600,
    pagination: {
        el: '.swiper-pagination',
        clickable: true,
    },
    autoplay: {
        delay: 3000,
        disableOnInteraction: false,
    }
});

// const calories = document.querySelector(".bmr-calculator .result .calories");
// const calculateBtn = document.querySelector(
//   ".bmr-calculator .result .calculate-btn"
// );
// const age = document.querySelector(".bmr-calculator form #age");
// const height = document.querySelector(".bmr-calculator form #height");
// const weight = document.querySelector(".bmr-calculator form #weight");
// const errorMessage = document.querySelector(
//   ".bmr-calculator .result .error-message"
// );


// const calculateBMR = (weight, height, age, gender) => {
//   if (gender == "male") {
//     return 10 * weight + 6.25 * height - 5 * age + 5;
//   }

//   return 10 * weight + 6.25 * height - 5 * age - 161;
// };

// calculateBtn.addEventListener("click", () => {
//   if (
//     age.classList.contains("invalid") ||
//     height.classList.contains("invalid") ||
//     weight.classList.contains("invalid")
//   ) {
//     errorMessage.classList.add("active");
//     return;
//   }

//   errorMessage.classList.remove("active");

//   let genderValue = document.querySelector(
//     ".bmr-calculator form input[name='gender']:checked"
//   ).value;

//   let BMR = calculateBMR(weight.value, height.value, age.value, genderValue);

//   calories.innerHTML = BMR.toLocaleString("en-US");
// });



// age.addEventListener("input", (e) => {
//   let ageValue = e.target.value;

//   if (!ageValue || isNaN(ageValue) || ageValue < 10 || ageValue > 100) {
//     age.classList.add("invalid");
//   } else {
//     age.classList.remove("invalid");
//   }
// });

// height.addEventListener("input", (e) => {
//   let heightValue = e.target.value;

//   if (!heightValue || isNaN(heightValue) || heightValue < 0) {
//     height.classList.add("invalid");
//   } else {
//     height.classList.remove("invalid");
//   }
// });

// weight.addEventListener("input", (e) => {
//   let weightValue = e.target.value;

//   if (!weightValue || isNaN(weightValue) || weightValue < 0) {
//     weight.classList.add("invalid");
//   } else {
//     weight.classList.remove("invalid");
//   }
// });

// // //////////////////   22
// const testimonialList = [
//     {
//       image: "http://placeskull.com/80/80/A4C2A5",
//       text: "Lorem ipsum dolor sit amet"
//     },
//     {
//       image: "http://placeskull.com/80/80/F49D37",
//       text: "Probny testimonial 2"
//     },
//     {
//       image: "http://placeskull.com/80/80/3F5E5A",
//       text: "A tutaj jeszcze jeden! mo alramaly and fgdjknk"
//     }
//   ];

//   let $image = null;
//   let $text = null;

//   const ONE_SECOND = 1000; 
//   const TIME_INTERVAL = 3 * ONE_SECOND;

//   function displayTestimonial(testimonialIndex) {
//     let testimonial = testimonialList[testimonialIndex];
//     $image.setAttribute('src', testimonial.image);
//     $text.textContent = testimonial.text;
//   }

//   function rotateTestimonial(index) {
//     displayTestimonial(index);

//     setInterval(() => {
//       index++;

//       if (index === testimonialList.length) {
//         index = 0;
//       }

//       displayTestimonial(index);
//     }, TIME_INTERVAL);
//   }

//   function setup() {
//     $image = document.querySelector('.testimonial__image');
//     $text = document.querySelector('.testimonial__text');

//     rotateTestimonial(0);
//   }

//   window.addEventListener('DOMContentLoaded', setup);
/*=============== EMAIL JS ===============*/

// const contactForm = document.getElementById('contact-form');
// contactMessage = document.getElementById('contact-message');

// const sendEmail = (e) => {
//     e.preventDefault()
//     emailjs.sendForm('service_2qmo3nm', 'template_74nnfqc', '#contact-form', 'yg9RrGdQ5Bpb2nGVr')
//         .then(() => {
//             contactMessage.textContent = 'Message sent successfully';

//             setTimeout(() => {
//                 contactMessage.textContent = '';
//             }, 500);

//             contactForm.reset()
//         }, () => {
//             contactMessage.textContent = 'Message failed to send (error)';
//         });
// };

// contactForm.addEventListener('submit', sendEmail);
/*=============== GSAP GLOBAL ANIMATIONS ===============*/
gsap.registerPlugin(ScrollTrigger)

/* العناصر اللي عايزينها تتأنيميت */
const animatedSections = document.querySelectorAll(`
.home__data,
.home__social,
.about__container,
.care__container,
.pricing__card,
.projects__content,
.testimonial__card,
.nutrantion__container,
.contact__container,
footer .row,
.accordion__item,
.containere
`)

/* ===== Sections Animation (CLEAN VERSION) ===== */
animatedSections.forEach((section) => {

    gsap.fromTo(section, {
        opacity: 0,
        y: 80
    }, {
        opacity: 1,
        y: 0,
        duration: 1.2,
        ease: "power3.out",
        scrollTrigger: {
            trigger: section,
            start: "top 85%",
            toggleActions: "play none none none"
        }
    })
})

/* ===== Hero Animation ===== */
gsap.from(".home__title", {
    opacity: 0,
    y: 120,
    duration: 2,
    ease: "power4.out"
})

gsap.from(".home__description", {
    opacity: 0,
    y: 80,
    duration: 1.8,
    delay: 0.4,
    ease: "power3.out"
})

gsap.from(".button", {
    opacity: 0,
    scale: 0.8,
    duration: 1.5,
    delay: 0.8,
    ease: "back.out(1.7)"
})

/* ===== Pricing Cards (Clean Scroll Animation) ===== */
gsap.utils.toArray('.pricing__card').forEach((card) => {

    gsap.fromTo(card, {
        opacity: 0,
        y: 80
    }, {
        opacity: 1,
        y: 0,
        duration: 1.2,
        ease: "power3.out",
        scrollTrigger: {
            trigger: card,
            start: "top 88%",
            toggleActions: "play none none none"
        }
    })
})

/* ===== Images Zoom Effect ===== */
gsap.utils.toArray(`
.about__img,
.care__img,
.nutrantion__img,
.projects__img
`).forEach((img) => {

    gsap.fromTo(img, {
        opacity: 0,
        scale: 0.85
    }, {
        opacity: 1,
        scale: 1,
        duration: 1.5,
        ease: "power3.out",
        scrollTrigger: {
            trigger: img,
            start: "top 85%",
            toggleActions: "play none none none"
        }
    })
})



// three js
/* ========== LOADER ========== */
const loader = document.getElementById("loader");
const progress = document.querySelector(".loader-progress");
const percentText = document.querySelector(".loader-percent");

let percent = 0;

function startLoader() {
    // Not every page includes the loader markup. Without this guard the
    // interval below throws on `progress.style` every 70ms, forever.
    if (!loader || !progress || !percentText) return;

    let loadInterval = setInterval(() => {
        percent += Math.floor(Math.random() * 6) + 2;

        if (percent >= 100) percent = 100;

        progress.style.width = percent + "%";
        percentText.textContent = percent + "%";

        if (percent === 100) {
            clearInterval(loadInterval);
            setTimeout(hideLoader, 300);
        }
    }, 70);
}

function hideLoader() {
    if (!loader) return;

    // gsap comes from a CDN; fall back to a plain hide if it is unavailable.
    if (typeof gsap === "undefined") {
        loader.style.display = "none";
        return;
    }

    gsap.to(loader, {
        opacity: 0,
        scale: 1.1,
        duration: 1,
        ease: "power3.out",
        onComplete: () => {
            loader.style.display = "none";
        }
    });
}

window.addEventListener("load", startLoader);