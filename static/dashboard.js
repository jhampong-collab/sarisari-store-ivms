'use strict';
const $=id=>document.getElementById(id), token=sessionStorage.getItem('sari_v2_test_token');
if(!token) location.replace('/');
const money=n=>'PHP '+Number(n||0).toLocaleString('en-PH',{minimumFractionDigits:2,maximumFractionDigits:2});
const msg=t=>{$('alert').textContent=t};
async function api(path,method='GET',body){const r=await fetch('/api/v2/'+path,{method,headers:{'Authorization':'Bearer '+token,'X-Requested-With':'sari-app','Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json().catch(()=>({}));if(!r.ok){if(r.status===401){sessionStorage.removeItem('sari_v2_test_token');location.replace('/')}throw Error(data.error||'Request failed');}return data;}
function cell(row,value){const td=document.createElement('td');td.textContent=String(value??'');row.append(td);return td;}
function option(select,value,label){const opt=document.createElement('option');opt.value=value;opt.textContent=label;select.append(opt);}
function selectOptions(id,items,label){const el=$(id);el.replaceChildren();option(el,'','Choose…');items.forEach(x=>option(el,x.id,label(x)));}
let products=[],customers=[],currentRole='owner';
async function load(){const dash=await api('dashboard');currentRole=dash.store.role;const roleLabels={owner:'Store Owner',manager:'Manager',cashier:'Cashier'};$('role-label').textContent=roleLabels[currentRole]||'Store member';$('role-avatar').textContent=({owner:'O',manager:'M',cashier:'C'})[currentRole]||'·';const [pr,cu]=await Promise.all([api('products'),currentRole==='cashier'?Promise.resolve([]):api('customers')]);document.querySelectorAll('[data-tab="staff"]').forEach(el=>el.hidden=currentRole!=='owner');document.querySelectorAll('[data-tab="credit"], [data-tab="reports"]').forEach(el=>el.hidden=currentRole==='cashier');$('product-form').hidden=false;$('customer-form').hidden=currentRole==='cashier';$('staff-tab').hidden=currentRole!=='owner';products=pr;customers=cu;$('store-name').textContent=dash.store.name;$('m-sales').textContent=money(dash.today_sales);$('m-products').textContent=dash.product_count;$('m-low').textContent=dash.low_stock.length;$('m-credit').textContent=money(dash.outstanding);$('m-trans').textContent=dash.today_transactions+' transactions';const low=$('low-list');low.replaceChildren();if(!dash.low_stock.length)low.textContent='All products are above their low-stock thresholds.';dash.low_stock.forEach(p=>{const row=document.createElement('div');const a=document.createElement('span'),b=document.createElement('strong');a.textContent=p.name;b.textContent=p.quantity+' left';row.append(a,b);low.append(row)});refreshInventoryCategories();renderProducts();renderCustomers();selectOptions('sale-product',products,p=>p.name+' · '+p.quantity+' available');selectOptions('credit-product',products,p=>p.name+' · '+p.quantity+' available');selectOptions('credit-customer',customers,c=>c.name+' · '+money(c.balance));}
function refreshInventoryCategories(){
 const el=$('inventory-category'),previous=el.value;
 const categories=[...new Set(products.map(p=>String(p.category||'').trim()).filter(Boolean))].sort((a,b)=>a.localeCompare(b));
 el.replaceChildren();option(el,'','All categories');categories.forEach(c=>option(el,c,c));
 el.value=categories.includes(previous)?previous:'';
}
function inventoryFilteredProducts(){
 const query=$('inventory-search').value.trim().toLocaleLowerCase();
 const category=$('inventory-category').value;
 const stock=$('inventory-stock').value;
 const sort=$('inventory-sort').value;
 const filtered=products.filter(p=>{
  const qty=Number(p.quantity)||0;
  const threshold=Number(p.reorder_level)||0;
  if(query&&!String(p.id||'').toLocaleLowerCase().includes(query)&&!String(p.name||'').toLocaleLowerCase().includes(query))return false;
  if(category&&String(p.category||'').trim()!==category)return false;
  if(stock==='out'&&qty!==0)return false;
  if(stock==='low'&&!(qty>0&&qty<=threshold))return false;
  if(stock==='available'&&qty<=0)return false;
  return true;
 });
 const compare=(a,b)=>String(a.name||'').localeCompare(String(b.name||''),undefined,{sensitivity:'base'});
 filtered.sort((a,b)=>{
  if(sort==='name-desc')return -compare(a,b);
  if(sort==='price-asc')return Number(a.price)-Number(b.price)||compare(a,b);
  if(sort==='price-desc')return Number(b.price)-Number(a.price)||compare(a,b);
  if(sort==='stock-asc')return Number(a.quantity)-Number(b.quantity)||compare(a,b);
  if(sort==='stock-desc')return Number(b.quantity)-Number(a.quantity)||compare(a,b);
  return compare(a,b);
 });
 return filtered;
}
['inventory-search','inventory-category','inventory-stock','inventory-sort'].forEach(id=>$(id).addEventListener('input',renderProducts));
function renderProducts(){const filtered=inventoryFilteredProducts();$('inventory-count').textContent=`Showing ${filtered.length} of ${products.length} products`;$('inventory-empty').hidden=filtered.length!==0;const body=$('product-rows');body.replaceChildren();filtered.forEach(p=>{const tr=document.createElement('tr');cell(tr,p.id);cell(tr,p.name);cell(tr,money(p.price));cell(tr,p.quantity);cell(tr,p.status);const td=document.createElement('td'),b=document.createElement('button');b.textContent='+ Stock';b.onclick=async()=>{const v=prompt('How many units to add to '+p.name+'?');if(v===null)return;try{await api('products/'+encodeURIComponent(p.id)+'/restock','POST',{quantity:Number(v)});await load();msg('Stock updated.')}catch(e){msg(e.message)}};td.append(b);tr.append(td);const editTd=document.createElement('td'),edit=document.createElement('button');edit.textContent='Edit';edit.onclick=()=>openProductEdit(p);editTd.append(edit);tr.append(editTd);body.append(tr)});}
function openProductEdit(p){
  const form=$('edit-product-form');
  form.elements.namedItem('name').value=p.name;
  form.elements.namedItem('category').value=p.category||'';
  form.elements.namedItem('price').value=p.price;
  form.elements.namedItem('cost').value=p.cost==null?'':p.cost;
  form.elements.namedItem('reorder_level').value=p.reorder_level;
  form.elements.namedItem('expiration').value=p.expiration||'';
  $('edit-product-id').textContent=p.id+' — '+p.name;
  form.dataset.productId=p.id;
  $('edit-product-panel').hidden=false;
  $('edit-product-panel').scrollIntoView({behavior:'smooth',block:'nearest'});
}
$('cancel-product-edit').onclick=()=>{$('edit-product-panel').hidden=true;};
$('edit-product-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget;
  const values=data(form);
  try{
    await api('products/'+encodeURIComponent(form.dataset.productId),'PATCH',values);
    $('edit-product-panel').hidden=true;
    await load();msg('Product details updated successfully.');
  }catch(err){msg(err.message);$('alert').scrollIntoView({behavior:'smooth',block:'nearest'});}
};
function renderCustomers(){const body=$('customer-list');body.replaceChildren();if(!customers.length)body.textContent='No customers yet.';customers.forEach(c=>{const row=document.createElement('div');const a=document.createElement('span'),b=document.createElement('strong');a.textContent=c.name+' ('+c.id+')';b.textContent=money(c.balance);row.append(a,b);body.append(row)})}
function data(form){return Object.fromEntries(new FormData(form).entries())}
function submit(id,fn){$(id).onsubmit=async e=>{e.preventDefault();try{await fn(data(e.target));e.target.reset();msg('Saved successfully.');await load()}catch(err){msg(err.message)}}}
submit('product-form',d=>api('products','POST',d));submit('sale-form',async d=>{const r=await api('sales','POST',d);msg('Sale recorded: '+money(r.total))});submit('customer-form',d=>api('customers','POST',d));submit('credit-form',d=>{const cid=encodeURIComponent(d.customer_id);return api('customers/'+cid+'/'+d.kind,'POST',d.kind==='charge'?{product_id:d.product_id,quantity:d.quantity}:{amount:d.amount})});
$('credit-kind').onchange=()=>{const payment=$('credit-kind').value==='payment';$('charge-field').hidden=payment;$('quantity-field').hidden=payment;$('payment-field').hidden=!payment};
async function report(){const d=await api('reports');const body=$('report-rows');body.replaceChildren();d.recent_sales.forEach(s=>{const tr=document.createElement('tr');cell(tr,s.product);cell(tr,s.quantity);cell(tr,money(s.total));cell(tr,s.at);body.append(tr)})}
let historyMovements=[];
async function loadHistory(){
 $('history-count').textContent='Loading history…';
 try{
  historyMovements=await api('inventory/movements?limit=200');
  renderHistory();
 }catch(err){$('history-count').textContent='Could not load history.';throw err;}
}
function renderHistory(){
 const search=$('history-search').value.trim().toLocaleLowerCase();
 const type=$('history-type').value;
 const from=$('history-from').value;
 const to=$('history-to').value;
 const rows=historyMovements.filter(m=>{
  const name=String(m.product_name||'').toLocaleLowerCase();
  const pid=String(m.product_id||'').toLocaleLowerCase();

const timestamp = new Date(m.created_at);
const day = Number.isNaN(timestamp.getTime())
  ? ''
  : new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Manila',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).format(timestamp);

  return (!search||name.includes(search)||pid.includes(search))&&
   (!type||m.movement_type===type)&&(!from||day>=from)&&(!to||day<=to);
 });
 $('history-count').textContent=`Showing ${rows.length} of ${historyMovements.length} recent transactions`;
 $('history-empty').hidden=rows.length!==0;
 const body=$('history-rows');body.replaceChildren();
 rows.forEach(m=>{
  const tr=document.createElement('tr');
  const date=new Date(m.created_at);
  cell(tr,Number.isNaN(date.getTime())?m.created_at:date.toLocaleString('en-PH', {timeZone:'Asia/Manila'}));
  cell(tr,`${m.product_id} — ${m.product_name}`);
  cell(tr,({STOCK_IN:'Stock in',STOCK_OUT:'Stock out',ADJUSTMENT:'Adjustment'})[m.movement_type]||m.movement_type);
  cell(tr,m.quantity_before);
  const change=Number(m.quantity_change);const td=cell(tr,change>0?`+${change}`:change);td.className=change>0?'history-positive':change<0?'history-negative':'';
  cell(tr,m.quantity_after);cell(tr,m.performed_by||'—');cell(tr,m.remarks||'—');body.append(tr);
 });
}
['history-search','history-type','history-from','history-to'].forEach(id=>$(id).addEventListener('input',renderHistory));
$('history-refresh').onclick=()=>loadHistory().catch(e=>msg(e.message));
function show(tab){document.querySelectorAll('.pane').forEach(p=>p.hidden=p.id!==tab);document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));if(tab==='reports')report().catch(e=>msg(e.message));if(tab==='staff')loadStaff().catch(e=>msg(e.message));if(tab==='history')loadHistory().catch(e=>msg(e.message));}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>show(b.dataset.tab));// Clear local session and ask Supabase to revoke this access token.
async function signOut(){
  const current=sessionStorage.getItem('sari_v2_test_token');
  sessionStorage.removeItem('sari_v2_test_token');
  try{
    const r=await fetch('/api/v2/config');
    const cfg=await r.json();
    if(current && cfg.url && cfg.key){
      await fetch(cfg.url.replace(/\/$/,'')+'/auth/v1/logout',{method:'POST',
        headers:{'apikey':cfg.key,'Authorization':'Bearer '+current}});
    }
  }catch(_e){/* Local logout still completes if offline. */}
  location.replace('/');
}
$('logout').onclick=signOut;
$('mobile-logout').onclick=signOut;
const THEME_KEY='intensity_v2_theme';
function applyTheme(theme){
  const dark=theme==='dark';
  document.documentElement.dataset.theme=dark?'dark':'light';
  const btn=$('theme-toggle');
  btn.innerHTML=dark?'☀ <span>Light mode</span>':'☾ <span>Dark mode</span>';
  btn.setAttribute('aria-label',dark?'Switch to light mode':'Switch to dark mode');
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content',dark?'#0b1426':'#f5f8fe');
}
applyTheme(localStorage.getItem(THEME_KEY)==='dark'?'dark':'light');
$('theme-toggle').onclick=()=>{
  const next=document.documentElement.dataset.theme==='dark'?'light':'dark';
  localStorage.setItem(THEME_KEY,next);applyTheme(next);
};
load().catch(e=>msg(e.message));

async function loadStaff(){
 if(currentRole!=='owner')return;
 const [invs,members]=await Promise.all([api('staff/invitations'),api('staff/members')]);
 const list=$('staff-invitations');list.replaceChildren();
 if(!invs.length)list.textContent='No invitations yet.';
 invs.forEach(inv=>{const row=document.createElement('div');row.className='staff-row';const label=document.createElement('span');label.textContent=inv.email+' — '+inv.role+' — '+inv.status;row.append(label);if(inv.status==='pending'){const copy=document.createElement('button');copy.textContent='Copy invite link';copy.onclick=()=>navigator.clipboard.writeText(location.origin+'/?invite='+encodeURIComponent(inv.id)).then(()=>msg('Invitation link copied. Send it privately to the invited staff email.')).catch(()=>msg('Copy failed. Please try again.'));const revoke=document.createElement('button');revoke.textContent='Revoke';revoke.onclick=async()=>{if(!confirm('Revoke this invitation?'))return;try{await api('staff/invitations/'+inv.id+'/revoke','POST',{});await loadStaff()}catch(e){msg(e.message)}};row.append(copy,revoke)}list.append(row)});
 const ml=$('staff-members');ml.replaceChildren();members.forEach(m=>{const row=document.createElement('div');row.className='staff-row';const label=document.createElement('span');label.textContent=m.email+' — '+m.role;row.append(label);if(m.role!=='owner'){const b=document.createElement('button');b.textContent='Remove access';b.onclick=async()=>{if(!confirm('Remove access for '+m.email+'?'))return;try{await api('staff/members/'+m.user_id+'/remove','POST',{});await loadStaff()}catch(e){msg(e.message)}};row.append(b)}ml.append(row)});
}
$('staff-invite-form').onsubmit=async e=>{e.preventDefault();try{const d=data(e.target);const inv=await api('staff/invitations','POST',d);$('invite-result').textContent='Invitation created for '+inv.email+'. Click Copy invite link below and send it privately. Automatic email delivery is not configured.';e.target.reset();await loadStaff()}catch(err){msg(err.message)}};
