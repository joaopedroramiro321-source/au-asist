const painelChat = $('#chat'), campoChat = $('#ask input'), logChat = $('#log');
const rotulos = {comparar:'Comparar fornecedores', preparar:'Preparar proposta', confirmar:'Confirmar planejamento',
  cancelar:'Cancelar proposta', analisar:'Analisar este produto', validade:'Consultar validade'};
let conversa = sessionStorage.getItem('einsan.conversa'), ocupado = false;
let vistas = new Set(JSON.parse(sessionStorage.getItem('einsan.sugestoes') || '[]'));

function abrirChat(abrir){
  painelChat.hidden = !abrir;
  $('#toggle').setAttribute('aria-expanded',String(abrir));
  (abrir ? campoChat : $('#toggle')).focus();
}
$('#toggle').onclick=()=>abrirChat(painelChat.hidden);
$('#fechar').onclick=()=>abrirChat(false);
painelChat.onkeydown=e=>{if(e.key==='Escape')abrirChat(false)};
function iniciar(){
  logChat.replaceChildren(el('p','Como posso ajudar com seu estoque?'));
  for(const q of ['O que preciso comprar?', 'Quanto tempo meu estoque dura?', 'Quais lotes estão em risco de vencer?', 'Comparar fornecedores']){
    logChat.append(button(q,()=>enviar(q)));
  }
}
$('#nova').onclick=()=>{
  conversa=null;sessionStorage.removeItem('einsan.conversa');campoChat.value='';iniciar();campoChat.focus();
};

function cartao(c){
  const box=el('article');
  function linha(s){box.append(el('div',s))}
  if(c.tipo==='proposta'){
    box.append(el('small',c.status+(c.versao?' · versão '+c.versao:'')),el('strong',money(c.total)));
    for(const i of c.itens)linha(i.nome+' · '+i.quantidade+' un. × '+money(i.unitario)+' · '+i.fornecedor+' · entrega em '+i.prazo+' dias · '+money(i.total));
  }else{
    box.append(el('b',c.nome));
    if(c.tipo==='pendencia')linha(c.motivo);
    if(c.tipo==='validade')linha('Lote '+c.lote+' · '+day(c.validade)+' · '+c.unidades+' un. em risco · '+money(c.perda));
    if(c.tipo==='cobertura')linha(c.saldo+' un. · '+(c.dias==null?'Sem consumo registrado':Number(c.dias).toLocaleString('pt-BR')+' dias'));
    if(c.tipo==='comparacao'){
      linha(c.fornecedor+' · '+money(c.unitario)+'/un. · mínimo '+c.minimo+' · '+c.prazo+' dias');
      linha(c.quantidade+' un. · '+money(c.total)+(c.atende?'':' · Chega após a ruptura'));
    }
  }
  return box;
}
function mostrar(r,restaurando=false){
  const box=el('div');box.className='resposta';
  box.append(el('p',r.resposta));
  const ctx=r.contexto;
  const filtros=[ctx.orcamento&&'Até '+money(ctx.orcamento),ctx.horizonte_dias&&ctx.horizonte_dias+' dias após entrega',ctx.fornecedor].filter(Boolean);
  if(filtros.length)box.append(el('small',filtros.join(' · ')));
  r.cartoes.forEach(c=>box.append(cartao(c)));
  for(const a of r.acoes)box.append(button(rotulos[a.tipo],()=>enviar(rotulos[a.tipo],a)));
  if(r.consulta==='compras'){
    for(const [label,value] of [['Ajustar orçamento','Até R$ '],['Alterar horizonte','Para ']]){
      box.append(button(label,()=>{campoChat.value=value;campoChat.focus()}));
    }
  }
  for(const s of r.sugestoes){
    if(restaurando || vistas.has(s.chave))continue;
    vistas.add(s.chave);sessionStorage.setItem('einsan.sugestoes',JSON.stringify([...vistas]));
    const n=el('div');n.append(button(s.motivo,()=>enviar(rotulos[s.acao.tipo],s.acao)),button('Dispensar',()=>n.remove()));box.append(n);
  }
  logChat.append(box);
}
function bloquear(valor){
  ocupado=valor;$('#nova').disabled=valor;$('#ask button').disabled=valor;
  $('#processando').textContent=valor?'Consultando…':'';
}
async function enviar(q,acao=null){
  if(ocupado)return;
  bloquear(true);
  const perto=()=>logChat.scrollHeight-logChat.scrollTop-logChat.clientHeight<100;
  const acompanhando=perto();
  const msg=el('p',q);msg.className='usuario';logChat.append(msg);
  if(acompanhando)logChat.scrollTop=logChat.scrollHeight;
  try{
    if(!conversa && logChat.querySelectorAll('.resposta').length===0)msg.scrollIntoView({block:'nearest'});
    const result=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pergunta:q,acao,conversa_id:conversa})});
    if(!result.ok)throw Error();
    const r=await result.json(),rolar=perto();
    conversa=r.conversa_id;sessionStorage.setItem('einsan.conversa',conversa);mostrar(r);
    if(campoChat.value.trim()===q)campoChat.value='';
    if(rolar)logChat.scrollTop=logChat.scrollHeight;
  }catch{
    const erro=el('div','Falha na consulta. ');erro.append(button('Tentar novamente',()=>{erro.remove();msg.remove();enviar(q,acao)}));logChat.append(erro);
  }finally{bloquear(false)}
}
$('#ask').onsubmit=e=>{e.preventDefault();if(campoChat.value.trim())enviar(campoChat.value.trim())};
function analisarProduto(i){abrirChat(true);enviar('Analisar '+i.nome,{tipo:'analisar',produto:i.id})}
async function restaurar(){
  iniciar();if(!conversa)return;
  bloquear(true);
  try{
    const response=await fetch('/conversas/'+conversa);
    if(!response.ok)throw Error();
    const mensagens=await response.json();logChat.replaceChildren();
    for(const m of mensagens){const n=el('p',m.pergunta);n.className='usuario';logChat.append(n);mostrar(m.resposta,true)}
    logChat.scrollTop=logChat.scrollHeight;
  }catch{logChat.append(el('p','Histórico indisponível.'),button('Tentar novamente',restaurar))}
  finally{bloquear(false)}
}
restaurar();
