'use strict';
document.getElementById('operations-login').addEventListener('submit', async event => {
  event.preventDefault();
  const status = document.getElementById('operations-status');
  const output = document.getElementById('operations-results');
  output.replaceChildren(); status.textContent = 'Loading…';
  try {
    const response = await fetch('/api/operations/summary', {headers: {'X-Operations-Token': document.getElementById('operations-key').value}, cache: 'no-store'});
    document.getElementById('operations-key').value = '';
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Unable to load operations');
    status.textContent = data.scope + ' Worker: ' + (data.generation_worker?.status || 'unavailable').replaceAll('_', ' ') + '.';
    const inferenceTitle=document.createElement('h2');inferenceTitle.textContent='Inference configuration';output.append(inferenceTitle);
    const inference=document.createElement('p');const config=data.inference;
    inference.textContent=config ? `${config.configuration_status || 'unknown'} · provider ${config.provider || 'none'} · model ${config.model_id || 'not set'}. Role: explanation labels only; ranking and food requirement checks remain rule-based. Configuration does not prove a successful model call.` : 'Inference configuration unavailable. No assumption is made about prior model calls.';output.append(inference);
    for (const [label, value] of [['Meal states', data.meal_states], ['Inbox reminders', data.inbox_jobs], ['Recommendation jobs', data.generation_jobs || {}]]) {
      const title = document.createElement('h2'); title.textContent = label; output.append(title);
      const paragraph = document.createElement('p'); paragraph.textContent = Object.entries(value).map(([state, count]) => `${state.replaceAll('_', ' ')}: ${count}`).join(' · ') || 'No events yet'; output.append(paragraph);
    }
    const historyTitle = document.createElement('h2'); historyTitle.textContent = 'Durable generation history'; output.append(historyTitle);
    for (const job of data.recent_generation_jobs || []) {
      const details = document.createElement('details'); const summary = document.createElement('summary');
      summary.textContent = `${job.status.replaceAll('_', ' ')} · version ${job.context_revision} · ${job.attempt_count}/${job.max_attempts} attempts`;
      details.append(summary);
      const identity = document.createElement('p'); identity.textContent = `Job ${job.id} · ${job.policy_version} · evidence ${job.evidence_revision}. Private inputs are not retained in receipts.`; details.append(identity);
      const list = document.createElement('ul');
      for (const attempt of job.attempts) {
        const metadata = attempt.metadata || {};
        const item = document.createElement('li'); item.textContent = `Attempt ${attempt.number}: ${attempt.status.replaceAll('_', ' ')} · ${attempt.duration_ms ?? 'unknown'} ms · tokens ${metadata.input_tokens ?? 'unknown'} in / ${metadata.output_tokens ?? 'unknown'} out${attempt.error_code ? ' · ' + attempt.error_code : ''}`;
        const inferenceLine=document.createElement('p');inferenceLine.textContent=`Inference: ${metadata.inference_status || metadata.model_status || 'not recorded'} · ${metadata.model_calls ?? 'unknown'} calls · provider ${metadata.provider || 'not recorded'} · model ${metadata.model_id || 'not recorded'}`;item.append(inferenceLine);
        const stages = document.createElement('ul');
        for (const stage of metadata.stages || []) { const line = document.createElement('li'); line.textContent = `${stage.stage} · ${stage.status} · ${stage.duration_ms ?? 'unknown'} ms`; stages.append(line); }
        item.append(stages); list.append(item);
      }
      details.append(list); output.append(details);
    }
    const title = document.createElement('h2'); title.textContent = 'Current recommendation stages'; output.append(title);
    for (const run of data.recent_runs) {
      const details = document.createElement('details'); const summary = document.createElement('summary');
      summary.textContent = `${run.status.replaceAll('_', ' ')} · ${run.eligible_count} options · ${run.model_calls} model calls · ${run.elapsed_ms ?? 'unknown'} ms`;
      details.append(summary);
      const inferenceLine=document.createElement('p');inferenceLine.textContent=`Inference: ${run.inference_status || run.model_status || 'not recorded'} · provider ${run.provider || 'not recorded'} · model ${run.model_id || 'not recorded'}`;details.append(inferenceLine);
      const usage = document.createElement('p'); usage.textContent = `Tokens: ${run.input_tokens ?? 'unknown'} in / ${run.output_tokens ?? 'unknown'} out (${run.usage_source}). Private values intentionally omitted.`; details.append(usage);
      const list = document.createElement('ul');
      for (const stage of run.stages) { const item = document.createElement('li'); item.textContent = `${stage.stage.replaceAll('_', ' ')} — ${stage.status} — ${stage.duration_ms ?? 'unknown'} ms`; list.append(item); }
      details.append(list); output.append(details);
    }
  } catch (error) { status.textContent = error.message; }
});
