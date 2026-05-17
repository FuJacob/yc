// ====== Progressive phone demo ======
const chatBody = document.getElementById('chatBody');
const typedPrompt = document.getElementById('typedPrompt');

const PLACEHOLDER = 'Ask anything…';

// One compelling, multi-topic conversation that progresses from empty → full.
const conversation = [
  { who: 'parent', label: 'Mom',     text: "Quick check — how's Emma doing in bio?" },
  { who: 'agent',                    text: "She's averaging 92% this quarter ↗\nGot an A on today's quiz." },
  { who: 'parent', label: 'Mom',     text: "Did Jake finish his homework?" },
  { who: 'agent',                    text: "Math ✓  Reading ✓\nHe's at soccer until 5 — you're driving today." },
  { who: 'kid',    label: 'Jake, 12', text: "can i go to sara's after?" },
  { who: 'agent',                    text: "Asking mom…" },
  { who: 'agent',                    text: "✅ Yes — home by 7. Added to the calendar." }
];

function setPlaceholder(on) {
  if (on) {
    typedPrompt.innerHTML = `<span class="placeholder">${PLACEHOLDER}</span>`;
  } else {
    typedPrompt.textContent = '';
  }
}

function makeBubble(msg) {
  const wrap = document.createElement('div');
  wrap.className = `bubble ${msg.who}`;
  if (msg.label) {
    const label = document.createElement('div');
    label.className = 'bubble-label';
    label.textContent = msg.label;
    wrap.appendChild(label);
  }
  const txt = document.createElement('div');
  txt.textContent = msg.text;
  txt.style.whiteSpace = 'pre-line';
  wrap.appendChild(txt);
  return wrap;
}

function makeTyping() {
  const t = document.createElement('div');
  t.className = 'typing';
  t.innerHTML = '<span></span><span></span><span></span>';
  return t;
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function typePrompt(text) {
  // clear any placeholder first
  typedPrompt.textContent = '';
  for (let i = 0; i < text.length; i++) {
    typedPrompt.textContent += text[i];
    await sleep(28 + Math.random() * 35);
  }
}

async function clearPrompt() {
  const txt = typedPrompt.textContent;
  for (let i = txt.length; i > 0; i--) {
    typedPrompt.textContent = txt.slice(0, i - 1);
    await sleep(10);
  }
}

function autoScroll() {
  chatBody.scrollTop = chatBody.scrollHeight;
}

async function fadeOutAll() {
  const children = Array.from(chatBody.children);
  chatBody.style.transition = 'opacity 0.6s';
  chatBody.style.opacity = '0';
  await sleep(600);
  chatBody.innerHTML = '';
  chatBody.style.opacity = '1';
}

async function runDemo() {
  // start completely empty with placeholder visible
  chatBody.innerHTML = '';
  setPlaceholder(true);
  await sleep(900);

  for (let i = 0; i < conversation.length; i++) {
    const msg = conversation[i];

    if (msg.who === 'parent' || msg.who === 'kid') {
      // clear placeholder, type the message in the input
      setPlaceholder(false);
      await typePrompt(msg.text);
      await sleep(380);
      await clearPrompt();
      chatBody.appendChild(makeBubble(msg));
      autoScroll();
      // restore placeholder unless next is also a parent/kid
      const next = conversation[i + 1];
      if (!next || next.who === 'agent') {
        setPlaceholder(true);
      }
      await sleep(550);
    } else {
      // agent: typing dots, then bubble
      const t = makeTyping();
      chatBody.appendChild(t);
      autoScroll();
      await sleep(850 + Math.random() * 500);
      t.remove();
      chatBody.appendChild(makeBubble(msg));
      autoScroll();
      await sleep(1100);
    }
  }

  // hold the full conversation for a while
  await sleep(8000);

  // fade and restart for new visitors
  await fadeOutAll();
  await sleep(400);
  runDemo();
}

// kick it off once the page is ready
function start() {
  setPlaceholder(true);
  setTimeout(runDemo, 700);
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', start);
} else {
  start();
}

// Pause background animations when tab hidden
document.addEventListener('visibilitychange', () => {
  document.body.style.animationPlayState = document.hidden ? 'paused' : 'running';
});

// Reveal-on-scroll for sections + cards
const observer = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.style.opacity = '1';
      entry.target.style.transform = 'translateY(0)';
    }
  });
}, { threshold: 0.08 });

document.querySelectorAll('.section, .feat, .step, .review, .mini-phone, .logo-strip').forEach(el => {
  el.style.opacity = '0';
  el.style.transform = 'translateY(16px)';
  el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
  observer.observe(el);
});
