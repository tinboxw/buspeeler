<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
const props=defineProps<{series:{name:string,data:[number,number|null][]}[]}>()
const el=ref<HTMLDivElement>(); let chart:echarts.ECharts|undefined; let observer:ResizeObserver
function draw(){chart?.setOption({animation:false,tooltip:{trigger:'axis'},legend:{top:0},grid:{left:65,right:25,top:45,bottom:60},xAxis:{type:'value',name:'时间 / s'},yAxis:{type:'value',scale:true},dataZoom:[{type:'inside'},{type:'slider'}],series:props.series.map(s=>({...s,type:'line',showSymbol:false,connectNulls:false,step:'end'}))},true)}
onMounted(()=>{chart=echarts.init(el.value!); observer=new ResizeObserver(()=>chart?.resize());observer.observe(el.value!);draw()})
watch(()=>props.series,draw,{deep:true})
onBeforeUnmount(()=>{observer?.disconnect();chart?.dispose()})
</script>
<template><div ref="el" class="chart" role="img" aria-label="CAN 候选与参考数据的时间曲线"></div></template>
