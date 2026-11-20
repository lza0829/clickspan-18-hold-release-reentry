<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const rows = ref([])
const reentries = ref([])
const batches = ref([])
const err = ref('')

onMounted(async () => {
  try {
    const [proofs, re, ba] = await Promise.all([
      api.proofs(),
      api.reentries(),
      api.releaseBatches(),
    ])
    rows.value = proofs
    reentries.value = re
    batches.value = ba
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <section class="panel">
    <h1>会话核证</h1>
    <p class="muted">会话击点集合与起止须与织机同源可核。</p>
    <p v-if="err" class="err">{{ err }}</p>
    <table class="data">
      <thead>
        <tr><th>会话</th><th>访客</th><th>点数</th><th>起止</th><th>击点集</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td class="mono">{{ r.session_id }}</td>
          <td>{{ r.visitor_id }}</td>
          <td>{{ r.hit_count }}</td>
          <td class="mono">{{ r.started_at }} → {{ r.ended_at }}</td>
          <td class="mono">{{ r.hit_ids_json }}</td>
        </tr>
      </tbody>
    </table>

    <h2 style="margin-top:32px">放行重裁核证（只读）</h2>
    <p class="muted">
      每次放行的门闸裁决轨迹：搁置键、放行批次、裁决后旧/新会话、是否切开、触发 hit。
      本表与放行批次均为只增边表，不就地改写。
    </p>
    <p v-if="!reentries.length" class="muted">暂无放行重裁记录。</p>
    <table v-else class="data">
      <thead>
        <tr>
          <th>搁置键</th><th>批次</th><th>访客</th><th>触发 hit</th>
          <th>旧会话</th><th>新会话</th><th>切开</th><th>门闸裁决</th><th>结果</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in reentries" :key="r.id">
          <td class="mono">{{ r.hold_key }}</td>
          <td class="mono">{{ r.release_batch_id }}</td>
          <td>{{ r.visitor_id }}</td>
          <td class="mono">{{ r.trigger_hit_id }}</td>
          <td class="mono">{{ r.old_session_id || '—' }}</td>
          <td class="mono">{{ r.new_session_id }}</td>
          <td>{{ r.split ? '是' : '否' }}</td>
          <td class="mono">{{ JSON.stringify(r.gate_decisions) }}</td>
          <td>{{ r.outcome }}</td>
        </tr>
      </tbody>
    </table>

    <h2 style="margin-top:32px">放行批次（只读）</h2>
    <p v-if="!batches.length" class="muted">暂无放行批次。</p>
    <table v-else class="data">
      <thead>
        <tr><th>批次</th><th>时刻</th><th>条数</th><th>结果</th><th>报告</th></tr>
      </thead>
      <tbody>
        <tr v-for="b in batches" :key="b.id">
          <td class="mono">{{ b.id }}</td>
          <td class="mono">{{ b.created_at }}</td>
          <td>{{ b.entry_count }}</td>
          <td>{{ b.result }}</td>
          <td class="mono">{{ JSON.stringify(b.report) }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
