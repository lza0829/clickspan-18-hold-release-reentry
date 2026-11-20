<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'

const rows = ref([])
const err = ref('')
const busy = ref(false)
const filter = ref('')

const visible = computed(() =>
  filter.value ? rows.value.filter((r) => r.status === filter.value) : rows.value,
)

async function load() {
  err.value = ''
  try {
    rows.value = await api.quarantine()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function releaseOne(r) {
  if (busy.value) return
  busy.value = true
  err.value = ''
  try {
    const res = await api.releaseHolds([r.id])
    const e = res.reentries[0]
    window.alert(
      `放行批次 ${res.batch_id}\n` +
        `击点 ${e.trigger_hit_id}：${e.split ? '切开另起 open' : '并入原 open'}\n` +
        `${e.old_session_id || '（无 open）'} → ${e.new_session_id}`,
    )
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="panel">
    <h1>搁置区</h1>
    <p class="muted">
      放行不等于直写成员：击点必须重新进入收流裁决（空闲切窗等门闸），
      同事务完成重裁、改挂与审计，重裁边表在「核证」页只读可查。
    </p>
    <p v-if="err" class="err">{{ err }}</p>
    <p class="muted">
      筛选：
      <a href="#" @click.prevent="filter = ''">全部</a> ·
      <a href="#" @click.prevent="filter = 'held'">held</a> ·
      <a href="#" @click.prevent="filter = 'released'">released</a>
    </p>
    <p v-if="!visible.length" class="muted">暂无搁置记录。</p>
    <table v-else class="data">
      <thead>
        <tr><th>ID</th><th>访客</th><th>原因</th><th>成员</th><th>状态</th><th>放行批次</th><th>时刻</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="r in visible" :key="r.id">
          <td>{{ r.id }}</td>
          <td>{{ r.visitor_id }}</td>
          <td>{{ r.reason }}</td>
          <td class="mono">{{ r.member_json }}</td>
          <td>{{ r.status }}</td>
          <td class="mono">{{ r.release_batch_id || '—' }}</td>
          <td class="mono">{{ r.created_at }}</td>
          <td>
            <button
              v-if="r.status === 'held'"
              :disabled="busy"
              @click="releaseOne(r)"
            >放行（重裁）</button>
            <span v-else class="muted">已放行</span>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
