// Password toggle utility
function initPasswordToggle(toggleId, inputId) {
    const toggle = document.getElementById(toggleId);
    const input = document.getElementById(inputId);
    if (!toggle || !input) return;

    toggle.addEventListener('click', () => {
        const type = input.getAttribute('type') === 'password' ? 'text' : 'password';
        input.setAttribute('type', type);
        toggle.textContent = type === 'password' ? '👁️' : '🙈';
    });
}

// Tab switching utility (profile.html)
function initTabs(tabSelector, contentSelector) {
    document.querySelectorAll(tabSelector).forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll(tabSelector).forEach(b => {
                b.classList.remove('active', 'text-indigo-400', 'bg-indigo-500/10');
            });
            btn.classList.add('active', 'text-indigo-400', 'bg-indigo-500/10');

            const tab = btn.dataset.tab;
            document.querySelectorAll(contentSelector).forEach(content => content.classList.add('hidden'));
            document.getElementById(tab + '-tab').classList.remove('hidden');
        });
    });
}

// Duplicate name checker (event.html)
function initDuplicateChecker(inputId, btnId, existingNames) {
    const input = document.getElementById(inputId);
    const btn = document.getElementById(btnId);
    if (!input || !btn) return;

    input.addEventListener('input', (e) => {
        const val = e.target.value.trim();
        if (existingNames.includes(val)) {
            input.style.background = 'rgba(0,0,0,0.5)';
            input.style.borderColor = 'rgba(239,68,68,0.4)';
            input.style.color = 'rgba(255,255,255,0.3)';
            btn.disabled = true;
            btn.innerText = "Вы уже в списке";
            btn.classList.add('opacity-50', 'cursor-not-allowed');
        } else {
            input.style.background = '';
            input.style.borderColor = '';
            input.style.color = '';
            btn.disabled = false;
            btn.innerText = "Подтвердить участие";
            btn.classList.remove('opacity-50', 'cursor-not-allowed');
        }
    });
}

// DOM ready helper
function ready(fn) {
    if (document.readyState !== 'loading') {
        fn();
    } else {
        document.addEventListener('DOMContentLoaded', fn);
    }
}
