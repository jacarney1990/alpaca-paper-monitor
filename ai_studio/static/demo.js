'use strict';
// A purely client-side, PUBLIC example. Never fetches private endpoints,
// uses provider keys, makes model requests, or presents current research.
(() => {
  const scenarios = {
    research: {
      topic: 'What should be verified before evaluating a new technology business?',
      contributions: [
        ['Researcher', 'Start with primary-source company disclosures. Establish what was announced, on what date, and which claims have independent support.'],
        ['Skeptic', 'Challenge assumptions about commercialization, funding, timelines, competitors and the reliability of quoted market figures.']
      ],
      review: 'Cross-review: the Skeptic asks the Researcher for sources; the Researcher asks the Skeptic to distinguish actual counterevidence from open questions.',
      synthesis: 'Illustrative consensus: collect first-party evidence, document disagreements, and list measurable milestones before drawing conclusions.'
    },
    fda: {
      topic: 'How can an FDA record be evaluated without mistaking it for a new approval?',
      contributions: [
        ['Regulatory researcher', 'Identify the exact FDA dataset, product name, application or recall identifier, and the dated original record.'],
        ['Risk analyst', 'Do not infer a drug is approved, safe, effective, or currently recalled from an unrelated openFDA listing.']
      ],
      review: 'Cross-review: confirm the record category and date, then check whether the original FDA source actually supports each statement.',
      synthesis: 'Illustrative consensus: link the official FDA record, classify what it documents, and flag clinical, regulatory, or causal questions that remain unresolved.'
    },
    project: {
      topic: 'How could a small team plan an Android-friendly research dashboard?',
      contributions: [
        ['Engineer', 'Define a minimum reliable workflow, mobile navigation, error handling, and automated offline tests.'],
        ['Project reviewer', 'Prioritize clear user permissions, low operating costs, and visible limitations instead of unverified feature claims.']
      ],
      review: 'Cross-review: compare engineering complexity with launch priorities, and explicitly note any untested integrations.',
      synthesis: 'Illustrative consensus: ship a narrow usable MVP, measure reliability, then add optional capabilities behind clear access and budget controls.'
    }
  };

  let active = 'research';
  const $ = id => document.getElementById(id);

  function renderResults() {
    const data = scenarios[active];
    const target = $('demo-results');
    target.replaceChildren();

    const intro = document.createElement('p');
    intro.className = 'preview-simulation-label';
    intro.textContent = 'PRACTICE WALKTHROUGH · PREWRITTEN SAMPLE · ZERO MODEL REQUESTS';
    target.appendChild(intro);

    for (const [name, content] of data.contributions) {
      const panel = document.createElement('section');
      panel.className = 'preview-review-card';
      const label = document.createElement('h3');
      label.textContent = '1 · Independent perspective: ' + name;
      const detail = document.createElement('p');
      detail.textContent = content;
      panel.append(label, detail);
      target.appendChild(panel);
    }
    for (const [index, value] of [data.review, data.synthesis].entries()) {
      const panel = document.createElement('section');
      panel.className = 'preview-review-card ' + (index ? 'preview-synthesis' : '');
      const label = document.createElement('h3');
      label.textContent = index ? '3 · Combined synthesis' : '2 · Team cross-review';
      const detail = document.createElement('p');
      detail.textContent = value;
      panel.append(label, detail);
      target.appendChild(panel);
    }

    const note = document.createElement('p');
    note.className = 'preview-simulation-label';
    note.textContent = 'Simulation complete. Real AI collaboration requires an authorized private workspace and provider usage limits.';
    target.appendChild(note);
  }

  for (const button of document.querySelectorAll('[data-demo]')) {
    button.addEventListener('click', () => {
      active = button.dataset.demo;
      for (const item of document.querySelectorAll('[data-demo]')) {
        const chosen = item.dataset.demo === active;
        item.classList.toggle('selected', chosen);
        item.setAttribute('aria-pressed', String(chosen));
      }
      $('demo-topic').textContent = scenarios[active].topic;
      $('demo-results').replaceChildren();
      const placeholder = document.createElement('p');
      placeholder.className = 'placeholder';
      placeholder.textContent = 'Ready to explore this sample scenario. Tap Run practice walkthrough.';
      $('demo-results').appendChild(placeholder);
    });
  }

  $('demo-run').addEventListener('click', renderResults);
})();
