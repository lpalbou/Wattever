/* Pure capability audit metrics. A failed rule does not establish wrong reasoning. */
(function(root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.WatteverCapabilityMetrics = api;
})(typeof window !== 'undefined' ? window : typeof globalThis !== 'undefined' ? globalThis : this, function() {
  'use strict';
  const OUTCOMES = ['pass','incorrect','truncated','error','cancelled','unscored','pending'];
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value || {}, key);
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const integer = value => Number.isInteger(value) && value >= 0;
  const compareText = (a,b) => a < b ? -1 : a > b ? 1 : 0;
  const object = value => value && typeof value === 'object' && !Array.isArray(value);
  const grade = row => object(row?.scoring) ? row.scoring : object(row?.grading) ? row.grading : object(row?.grade) ? row.grade : {};
  const truncated = row => row?.finish_reason === 'length' || grade(row).truncated === true;
  const isScored = row => row?.status === 'ok' && typeof grade(row).correct === 'boolean';
  const correct = row => isScored(row) ? grade(row).correct : null;

  function classifyOutcome(row) {
    if (!object(row)) return 'unscored';
    const status = typeof row.status === 'string' ? row.status.toLowerCase() : '';
    if (row.pending === true || status === 'pending' || status === 'planned') return 'pending';
    if (status === 'cancelled' || status === 'canceled') return 'cancelled';
    if (['error','failed','timeout','transport_error'].includes(status)) return 'error';
    // The recorded boolean is authoritative. Preserve a recorded pass even if
    // another field inconsistently claims truncation; never re-grade an answer.
    if (isScored(row)) return correct(row) ? 'pass' : truncated(row) ? 'truncated' : 'incorrect';
    return truncated(row) ? 'truncated' : 'unscored';
  }

  function taskList(run) {
    const tasks = run?.suite?.tasks ?? run?.suite?.manifest?.tasks;
    return Array.isArray(tasks) ? tasks.filter(task => object(task) && typeof task.id === 'string') : [];
  }
  function originalRows(run) { return Array.isArray(run?.results) ? run.results.filter(object) : []; }
  function itemId(row, index) { return String(row.item_id ?? row.task_id ?? ('observation-' + String(index + 1).padStart(4,'0'))); }
  function itemIds(run) {
    const selected = run?.protocol?.selected_item_ids;
    if (Array.isArray(selected) && selected.length) return [...new Set(selected.filter(id => typeof id === 'string'))].sort(compareText);
    const tasks = taskList(run);
    return [...new Set(tasks.length ? tasks.map(task => task.id) : originalRows(run).map(itemId))].sort(compareText);
  }
  function repeatIds(run) {
    const configured = run?.protocol?.repeats ?? run?.config?.repeats;
    let count = Number.isInteger(configured) && configured > 0 ? configured : 0;
    if (!count) count = originalRows(run).reduce((max,row) => integer(row.repeat) ? Math.max(max,row.repeat+1) : max, 1);
    return Array.from({length:Math.min(count,10000)}, (_,index) => index);
  }
  function hasReportedOnly(run) {
    const score = run?.summary;
    return (!Array.isArray(run?.results) || run.results.length === 0) && object(score) &&
      (finite(score.scored) && (score.scored > 0 || !Array.isArray(run?.results)) || finite(score.accuracy) || finite(score.percent));
  }

  function rows(run) {
    if (!object(run) || hasReportedOnly(run)) return [];
    const ids = itemIds(run), repeats = repeatIds(run);
    const tasks = new Map(taskList(run).map(task => [task.id,task]));
    const observed = new Map();
    originalRows(run).forEach((row,index) => {
      const id = itemId(row,index), repeat = integer(row.repeat) ? row.repeat : 0;
      const key = JSON.stringify([id,repeat]);
      if (!observed.has(key)) observed.set(key,{row,index,duplicates:0});
      else observed.get(key).duplicates++;
    });
    const cells = [];
    ids.forEach(id => repeats.forEach(repeat => {
      const entry = observed.get(JSON.stringify([id,repeat])), task = tasks.get(id) || null;
      const cell = entry ? {...entry.row} : {status:'pending',pending:true};
      cell.item_id = id;
      cell.repeat = repeat;
      cell.category = typeof task?.category === 'string' ? task.category : typeof cell.category === 'string' ? cell.category : 'uncategorized';
      cell.task = task;
      cell.observed = !!entry;
      cell.pending = !entry || cell.pending === true || cell.status === 'pending';
      cell.duplicate_observations = entry?.duplicates || 0;
      cell.outcome = classifyOutcome(cell);
      cells.push(cell);
    }));
    return cells;
  }

  function selected(value) {
    return value == null || value === '' || value === 'all' ? null : Array.isArray(value) ? value : [value];
  }
  const hasQuery = filters => typeof filters?.query === 'string' && !!filters.query.trim();
  function filterCells(cells, filters) {
    filters = filters || {};
    const categories = selected(filters.category), outcomes = selected(filters.outcome), repeats = selected(filters.repeat);
    const terms = typeof filters.query === 'string' ? filters.query.toLowerCase().trim().split(/\s+/).filter(Boolean) : [];
    return cells.filter(row => {
      if (categories && !categories.includes(row.category)) return false;
      if (repeats && !repeats.some(value => String(value) === String(row.repeat))) return false;
      if (outcomes && !outcomes.some(value => value === row.outcome || value === 'failed_rule' && correct(row) === false)) return false;
      if (!terms.length) return true;
      const searchable = [row.item_id,row.category,row.task?.prompt,row.response,row.response_text,row.output,grade(row).reason,
        row.diagnostic,row.error].filter(value => value != null).map(value => typeof value === 'string' ? value : JSON.stringify(value)).join(' ').toLowerCase();
      return terms.every(term => searchable.includes(term));
    });
  }
  function filterRows(run, filters) { return filterCells(rows(run),filters); }

  function stats(cells) {
    const counts = Object.fromEntries(OUTCOMES.map(outcome => [outcome,0]));
    cells.forEach(row => counts[row.outcome]++);
    const scored = cells.filter(isScored), pass = scored.filter(row => correct(row) === true).length;
    const planned = cells.length, attempted = cells.filter(row => row.observed && row.outcome !== 'pending').length;
    return {counts,planned,scored:scored.length,pass,correct:pass,failed_rule:scored.length-pass,
      accuracy:scored.length ? pass/scored.length : null,coverage:planned ? scored.length/planned : null,
      attempted,response_coverage:planned ? attempted/planned : null,
      transport_errors:counts.error,truncation_count:cells.filter(truncated).length,
      truncated_unscored:cells.filter(row => truncated(row) && !isScored(row)).length,
      duplicate_observations:cells.reduce((sum,row) => sum+row.duplicate_observations,0)};
  }

  function reportedStats(score) {
    score = object(score) ? score : {};
    const scored = integer(score.scored) ? score.scored : null;
    let pass = integer(score.correct) ? score.correct : integer(score.pass) ? score.pass : null;
    const accuracy = finite(score.accuracy) ? score.accuracy : finite(score.percent) ? score.percent/100 : pass != null && scored ? pass/scored : null;
    if (pass == null && scored != null && accuracy != null && Math.abs(scored*accuracy-Math.round(scored*accuracy)) < 1e-7) pass = Math.round(scored*accuracy);
    const errors = integer(score.errors) ? score.errors : 0, cancelled = integer(score.cancelled) ? score.cancelled : 0;
    const planned = integer(score.planned) ? score.planned : integer(score.attempts) ? score.attempts : scored != null ? scored+errors+cancelled : null;
    const truncation = integer(score.truncated) ? score.truncated : 0;
    const incorrect = scored != null && pass != null ? Math.max(0,scored-pass) : null;
    return {counts:{pass,incorrect:incorrect == null ? null : Math.max(0,incorrect-truncation),truncated:truncation,error:errors,cancelled,unscored:0,
        pending:planned != null && scored != null ? Math.max(0,planned-scored-errors-cancelled) : 0},
      planned,scored,pass,correct:pass,failed_rule:incorrect,accuracy,
      coverage:planned && scored != null ? scored/planned : null,
      attempted:integer(score.attempts) ? score.attempts : scored != null ? scored+errors+cancelled : null,
      response_coverage:null,transport_errors:errors,truncation_count:truncation,truncated_unscored:null,duplicate_observations:0};
  }

  function ciFor(run,filters) {
    const outcome = selected(filters?.outcome), repeat = selected(filters?.repeat), categories = selected(filters?.category);
    if (outcome || hasQuery(filters) || repeat && !(repeat.length===1 && String(repeat[0])==='0')) return null;
    if (categories) {
      if (categories.length!==1) return null;
      return (run?.summary?.category_scores || []).find(score => score.category===categories[0])?.wilson_95 || null;
    }
    return run?.summary?.wilson_95 || run?.summary?.first_repeat_ci || null;
  }
  function summary(run,filters) {
    const cells = filterRows(run,filters), fallback = hasReportedOnly(run);
    let result;
    if (fallback && !selected(filters?.outcome) && !selected(filters?.repeat) && !hasQuery(filters)) {
      const categories = selected(filters?.category);
      const score = categories?.length===1 ? (run.summary.category_scores || []).find(value => value.category===categories[0]) : !categories ? run.summary : null;
      result = reportedStats(score);
    } else result = stats(cells);
    const categoryNames = fallback ? (run?.summary?.category_scores || []).map(value => value.category).sort(compareText) : [...new Set(cells.map(row => row.category))].sort(compareText);
    result.categories = categoryNames.filter(category => !selected(filters?.category) || selected(filters.category).includes(category)).map(category => ({category,
      ...(fallback ? reportedStats((run.summary.category_scores || []).find(value => value.category===category)) : stats(cells.filter(row => row.category===category)))}));
    result.repeats = fallback ? (run?.summary?.repeat_scores || []).map(value => ({repeat:value.repeat,...reportedStats(value)})) : repeatIds(run).filter(repeat => !selected(filters?.repeat) || selected(filters.repeat).some(value => String(value)===String(repeat))).map(repeat => ({repeat,...stats(cells.filter(row => row.repeat===repeat))}));
    result.first_repeat_ci = ciFor(run,filters);
    result.wilson_95 = result.first_repeat_ci;
    result.source = fallback ? 'reported_summary; item audit unavailable' : Array.isArray(run?.results) ? 'item_audit' : 'unavailable';
    result.audit_available = !fallback && Array.isArray(run?.results);
    result.unique_items = fallback ? (run?.summary?.unique_items ?? null) : new Set(cells.map(row => row.item_id)).size;
    result.score_interpretation = 'Recorded rule passes; a rule failure does not establish incorrect reasoning. Errors are not scored answers.';
    return result;
  }

  function matrix(run) {
    const cells = rows(run), ids = itemIds(run), repeats = repeatIds(run);
    const grouped = new Map(ids.map(id => [id,[]]));
    cells.forEach(row => grouped.get(row.item_id).push(row));
    return {itemIds:ids,repeats,audit_available:!hasReportedOnly(run) && Array.isArray(run?.results),rows:cells.length ? ids.map(id => {
      const matches = grouped.get(id);
      return {item_id:id,category:matches[0]?.category || 'uncategorized',task:matches[0]?.task || null,cells:matches};
    }) : []};
  }
  function hotspots(run) {
    return matrix(run).rows.map(item => {
      const result = stats(item.cells), scored = item.cells.filter(isScored);
      const varies = new Set(scored.map(correct)).size > 1;
      return {...item,...result,failed_rule_count:result.failed_rule,invalid_count:item.cells.filter(row => row.outcome!=='pending' && !isScored(row)).length,
        variance:varies,varies,scored_repeats:scored.map(row => row.repeat),correctness_values:scored.map(correct)};
    }).filter(item => item.failed_rule_count || item.invalid_count || item.varies)
      .sort((a,b) => b.failed_rule_count-a.failed_rule_count || Number(b.varies)-Number(a.varies) || b.invalid_count-a.invalid_count || compareText(a.item_id,b.item_id));
  }

  function stable(value) {
    if (Array.isArray(value)) return '['+value.map(stable).join(',')+']';
    if (object(value)) return '{'+Object.keys(value).sort().map(key => JSON.stringify(key)+':'+stable(value[key])).join(',')+'}';
    return JSON.stringify(value);
  }
  function completeProtocol(run) {
    const p = run?.protocol;
    return object(p) && typeof p.version==='string' && !!p.version && typeof p.scoring_version==='string' && !!p.scoring_version &&
      typeof p.suite_digest==='string' && !!p.suite_digest && Array.isArray(p.selected_item_ids) && p.selected_item_ids.length>0 &&
      p.selected_item_ids.every(id => typeof id==='string' && !!id) && new Set(p.selected_item_ids).size===p.selected_item_ids.length &&
      object(p.sampling) && own(p.sampling,'temperature') && own(p.sampling,'top_p') && own(p.sampling,'seed') &&
      (p.sampling.temperature===null || finite(p.sampling.temperature) && p.sampling.temperature>=0 && p.sampling.temperature<=2) &&
      (p.sampling.top_p===null || finite(p.sampling.top_p) && p.sampling.top_p>0 && p.sampling.top_p<=1) && integer(p.sampling.seed) &&
      Number.isInteger(p.max_tokens) && p.max_tokens>0;
  }
  function compareRuns(a,b) {
    const reasons = [], pa=a?.protocol || {}, pb=b?.protocol || {};
    if (typeof a?.evaluation_key!=='string' || !a.evaluation_key || typeof b?.evaluation_key!=='string' || !b.evaluation_key) reasons.push('Evaluation fingerprint is missing.');
    else if (a.evaluation_key!==b.evaluation_key) reasons.push('Evaluation fingerprints differ.');
    if (!completeProtocol(a) || !completeProtocol(b)) reasons.push('Declared evaluation protocol is incomplete.');
    if ([a,b].some(run => run?.suite_digest && run.suite_digest!==run?.protocol?.suite_digest || run?.suite?.digest && run.suite.digest!==run?.protocol?.suite_digest)) reasons.push('Suite metadata contradicts the declared protocol.');
    if (pa.suite_digest!==pb.suite_digest) reasons.push('Suite digests differ.');
    if (pa.scoring_version!==pb.scoring_version) reasons.push('Scoring rules differ.');
    if (stable(pa.selected_item_ids)!==stable(pb.selected_item_ids)) reasons.push('Selected item IDs differ.');
    const policy = p => Object.fromEntries(Object.entries(p).filter(([key]) => key!=='repeats'));
    if (stable(policy(pa))!==stable(policy(pb))) reasons.push('Sampling, output budget or evaluation policy differs.');
    const eligible = reasons.length===0, ar=rows(a), br=rows(b);
    const key = row => JSON.stringify([row.item_id,row.repeat]);
    const am=new Map(ar.map(row => [key(row),row])), bm=new Map(br.map(row => [key(row),row]));
    const paired={both_correct:0,only_a:0,only_b:0,both_incorrect:0,one_unscored:0,both_unscored:0,unmatched:0,overlap:0,both_scored:0};
    [...new Set([...am.keys(),...bm.keys()])].forEach(id => {
      const ra=am.get(id), rb=bm.get(id);
      if (!ra || !rb) {paired.unmatched++;return;}
      paired.overlap++;
      const ca=correct(ra), cb=correct(rb);
      if (ca===null && cb===null) paired.both_unscored++;
      else if (ca===null || cb===null) paired.one_unscored++;
      else {
        paired.both_scored++;
        if (ca && cb) paired.both_correct++;
        else if (ca) paired.only_a++;
        else if (cb) paired.only_b++;
        else paired.both_incorrect++;
      }
    });
    const sa=summary(a), sb=summary(b);
    return {eligible,reasons,difference_pp:eligible && finite(sa.accuracy) && finite(sb.accuracy) ? 100*(sa.accuracy-sb.accuracy) : null,
      difference_direction:'A minus B',difference_scope:'Observed scored-answer accuracy; denominators may differ. Inspect coverage and paired observations.',
      paired,paired_difference_pp:eligible && paired.both_scored ? 100*(paired.only_a-paired.only_b)/paired.both_scored : null,
      descriptive_only:!eligible,scope:eligible ? 'Matching declared evaluation policy; repeated observations are not independent items.' : 'Overlapping observations are descriptive only; these policies do not establish a fair paired comparison.'};
  }
  return {OUTCOMES:OUTCOMES.slice(),classifyOutcome,rows,filterRows,summary,matrix,hotspots,compareRuns};
});
