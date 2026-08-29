/* Thai and English for the dashboard and the admin console.
 *
 * The English text is the key, so a string with no entry below falls back to
 * English rather than showing a missing-key placeholder. Adding a language
 * means adding a dictionary; adding a string needs no key invented for it.
 *
 * Thai is the default because that is what the members read. The choice is
 * remembered per browser.
 */

const STORE = 'signal-lang';
export const LANGUAGES = [
  ['th', 'ไทย'],
  ['en', 'EN'],
];

const TH = {
  // ------------------------------------------------------------ navigation
  'Overview': 'ภาพรวม',
  'Signals': 'สัญญาณ',
  'Daily': 'รายวัน',
  'Weekly': 'รายสัปดาห์',
  'Monthly': 'รายเดือน',
  'Analytics': 'วิเคราะห์',
  'Methodology': 'วิธีคำนวณ',
  'Messages': 'ข้อความ',
  'Sent to LINE': 'ที่ส่งเข้า LINE',
  'Edit history': 'ประวัติการแก้ไข',
  'Statistics': 'สถิติ',
  'Audit log': 'บันทึกการตรวจสอบ',
  'System status': 'สถานะระบบ',
  'Settings': 'ตั้งค่า',
  '← Back to signals': '← กลับไปหน้าสัญญาณ',

  // --------------------------------------------------------------- summary
  'Performance summary': 'สรุปผลงาน',
  'Total Signals': 'สัญญาณทั้งหมด',
  'Total signals': 'สัญญาณทั้งหมด',
  'Win Rate': 'อัตราชนะ',
  'Win rate': 'อัตราชนะ',
  'Total P/L': 'กำไร/ขาดทุนรวม',
  'Wins': 'ชนะ',
  'Losses': 'แพ้',
  'Break-even': 'เสมอตัว',
  'Profit Factor': 'Profit Factor',
  'Profit factor': 'Profit Factor',
  'Max Drawdown': 'ขาดทุนสูงสุดต่อเนื่อง',
  'Max drawdown': 'ขาดทุนสูงสุดต่อเนื่อง',
  'Average Win': 'กำไรเฉลี่ยต่อไม้ที่ชนะ',
  'Average Loss': 'ขาดทุนเฉลี่ยต่อไม้ที่แพ้',
  'Average P/L': 'กำไร/ขาดทุนเฉลี่ย',
  'Expectancy': 'ค่าคาดหวังต่อไม้',
  'Best / Worst': 'ดีสุด / แย่สุด',
  'Longest Win Streak': 'ชนะติดกันมากสุด',
  'Longest Loss Streak': 'แพ้ติดกันมากสุด',
  'Avg Risk': 'ความเสี่ยงเฉลี่ย',
  'Avg Reward': 'ผลตอบแทนเฉลี่ย',
  'Risk : Reward': 'เสี่ยง : ได้',
  'Risk : reward': 'เสี่ยง : ได้',
  'Open signals': 'สัญญาณที่ยังเปิดอยู่',
  'Not included in the win rate: ': 'ไม่นับในอัตราชนะ: ',

  // ---------------------------------------------------------------- charts
  'Cumulative P/L': 'กำไร/ขาดทุนสะสม',
  'Cumulative points': 'points สะสม',
  'Daily P/L (last 14 days with signals)': 'กำไร/ขาดทุนรายวัน (14 วันล่าสุดที่มีสัญญาณ)',
  'How far trades ran': 'ไม้ไปได้ไกลแค่ไหน',
  'Distribution of results (points)': 'การกระจายของผลลัพธ์ (points)',
  'By direction': 'แยกตามทิศทาง',
  'By symbol': 'แยกตามสินค้า',
  'Win rate by hour of day': 'อัตราชนะตามชั่วโมง',
  'Win rate by weekday': 'อัตราชนะตามวันในสัปดาห์',
  'Local time. Hours with no signals are omitted.': 'เวลาท้องถิ่น ชั่วโมงที่ไม่มีสัญญาณจะไม่แสดง',
  'Take profits are a ladder: a signal that reached TP2 also counts under TP1.':
    'TP นับเป็นขั้นบันได: สัญญาณที่ถึง TP2 จะถูกนับใน TP1 ด้วย',

  // ------------------------------------------------------------ table heads
  'Time': 'เวลา',
  'Posted': 'เวลาที่โพสต์',
  'Received': 'เวลาที่รับเข้า',
  'Signal': 'สัญญาณ',
  'Direction': 'ทิศทาง',
  'Entry': 'ราคาเข้า',
  'SL': 'SL',
  'TP1': 'TP1',
  'TP2': 'TP2',
  'Status': 'สถานะ',
  'Result': 'ผล',
  'P/L': 'กำไร/ขาดทุน',
  'Detail': 'รายละเอียด',
  'Type': 'ชนิด',
  'Content': 'เนื้อหา',
  'Tries': 'ครั้งที่ลอง',
  'Event': 'เหตุการณ์',
  'Entity': 'รายการ',
  'Actor': 'ผู้กระทำ',
  'When': 'เมื่อไร',
  'What happened': 'เกิดอะไรขึ้น',
  'Component': 'ส่วนประกอบ',
  'Components': 'ส่วนประกอบ',
  'Last heartbeat': 'สัญญาณชีพล่าสุด',
  'Total': 'รวม',
  'Provider': 'ผู้ให้บริการ',

  // ------------------------------------------------------------- statuses
  'Pending': 'รอผล',
  'Cancelled': 'ยกเลิก',
  'Ambiguous': 'ตัดสินไม่ได้',
  'Complete': 'ครบถ้วน',
  'INCOMPLETE': 'ข้อมูลไม่ครบ',
  'Sent': 'ส่งแล้ว',
  'Failed': 'ล้มเหลว',
  'Skipped': 'ข้าม',
  'Read': 'อ่านแล้ว',
  'TP1 Hit': 'ถึง TP1',
  'TP2 Hit': 'ถึง TP2',
  'TP3 Hit': 'ถึง TP3',
  'SL Hit': 'ชน SL',
  'On': 'เปิด',
  'Off': 'ปิด',
  'All': 'ทั้งหมด',
  'All time': 'ทั้งหมด',
  'All events': 'ทุกเหตุการณ์',
  'All statuses': 'ทุกสถานะ',
  'Custom range…': 'กำหนดช่วงเอง…',
  'Previous': 'ก่อนหน้า',
  'Next': 'ถัดไป',
  'Requeue': 'ส่งใหม่',
  'Requeued for delivery.': 'เข้าคิวส่งใหม่แล้ว',

  // ------------------------------------------------------- signal detail
  'Stop Loss': 'จุดตัดขาดทุน',
  'Take Profit 1': 'เป้าหมาย 1',
  'Take Profit 2': 'เป้าหมาย 2',
  'Take Profit 3': 'เป้าหมาย 3',
  'Entry filled': 'เข้าไม้เมื่อ',
  'Resolved': 'ตัดสินผลเมื่อ',
  'Risk': 'ความเสี่ยง',
  'Reward': 'ผลตอบแทน',
  'Signal ID': 'รหัสสัญญาณ',
  'Telegram message ID': 'รหัสข้อความ Telegram',
  'Telegram chat ID': 'รหัสกลุ่ม Telegram',
  'Version': 'เวอร์ชัน',
  'How each version was read': 'ระบบอ่านแต่ละเวอร์ชันได้ว่าอย่างไร',
  'Telegram → LINE messages': 'ข้อความ Telegram → LINE',
  'Note: ': 'หมายเหตุ: ',

  // ---------------------------------------------------------- empty states
  'No data yet.': 'ยังไม่มีข้อมูล',
  'No daily data yet.': 'ยังไม่มีข้อมูลรายวัน',
  'No decided signals yet.': 'ยังไม่มีสัญญาณที่ตัดสินผลแล้ว',
  'No decided signals in this range yet.': 'ยังไม่มีสัญญาณที่ตัดสินผลแล้วในช่วงนี้',
  'No signals recorded yet.': 'ยังไม่มีสัญญาณที่บันทึกไว้',
  'No signals match this filter yet.': 'ไม่มีสัญญาณที่ตรงกับตัวกรองนี้',
  'No messages recorded yet.': 'ยังไม่มีข้อความที่บันทึกไว้',
  'No message has been edited yet.': 'ยังไม่มีข้อความที่ถูกแก้ไข',
  'Nothing recorded yet.': 'ยังไม่มีอะไรบันทึกไว้',
  'Nothing matches that filter.': 'ไม่มีอะไรตรงกับตัวกรองนั้น',
  'Nothing changed.': 'ไม่มีอะไรเปลี่ยนแปลง',
  'No component has reported in yet. Start the bridge with python -m app.main.':
    'ยังไม่มีส่วนประกอบใดรายงานเข้ามา เริ่มระบบด้วย python -m app.main',

  // ------------------------------------------------------------ admin only
  'Administrator sign-in': 'เข้าสู่ระบบผู้ดูแล',
  'Admin password': 'รหัสผ่านผู้ดูแล',
  'Sign in': 'เข้าสู่ระบบ',
  'Signed in as': 'เข้าสู่ระบบในชื่อ',
  'ADMIN_PASSWORD from the server environment. Sign-ins, successful or not, are recorded in the audit log.':
    'ใช้ ADMIN_PASSWORD จากไฟล์ตั้งค่าบนเซิร์ฟเวอร์ การเข้าสู่ระบบทุกครั้ง ทั้งสำเร็จและไม่สำเร็จ ถูกบันทึกไว้',
  'Connections': 'การเชื่อมต่อ',
  'Telegram source': 'ต้นทาง Telegram',
  'LINE configured': 'ตั้งค่า LINE แล้ว',
  'Price data': 'ข้อมูลราคา',
  'Delivery queue': 'คิวส่งข้อความ',
  'Edited messages': 'ข้อความที่ถูกแก้ไข',
  'Test LINE credentials': 'ทดสอบการเชื่อมต่อ LINE',
  'Test LINE now': 'ทดสอบ LINE เดี๋ยวนี้',
  'Test {target} now': 'ทดสอบ {target} เดี๋ยวนี้',
  'Save and restart': 'บันทึกแล้วรีสตาร์ต',

  // ------------------------------------------------ settings group titles
  'Where messages go': 'ส่งข้อความไปที่ไหน',
  'Telegram': 'Telegram',
  'LINE': 'LINE',
  'Results and prices': 'ผลและราคา',
  'Dashboard': 'แดชบอร์ด',
  'Other settings': 'ค่าอื่นๆ',
  'Settings in force': 'ค่าที่ใช้งานอยู่',
  'These are the live settings. ': 'นี่คือค่าที่ระบบใช้งานจริงอยู่ ',
  'Search text or message id…': 'ค้นหาข้อความ หรือรหัสข้อความ…',
  'every version is kept; nothing here can be deleted':
    'ทุกเวอร์ชันถูกเก็บไว้ ไม่มีอะไรในนี้ลบได้',
  'These numbers cannot be edited. ': 'ตัวเลขเหล่านี้แก้ไขด้วยมือไม่ได้ ',
  'Statistics cannot be typed in by an administrator — they are computed from the signals table.':
    'ผู้ดูแลพิมพ์ตัวเลขสถิติเองไม่ได้ ทุกตัวคำนวณจากตารางสัญญาณเท่านั้น',
  'A correction made by hand is written to the audit log with the old value, the new value, who changed it, when, and why.':
    'การแก้ไขด้วยมือทุกครั้งถูกบันทึกไว้ พร้อมค่าเดิม ค่าใหม่ ใครแก้ เมื่อไร และเพราะอะไร',
  'A component that has not checked in for two minutes is shown as DOWN.':
    'ส่วนประกอบที่ไม่รายงานตัวเกินสองนาทีจะแสดงเป็น DOWN',
  'Available providers': 'ผู้ให้บริการที่มีให้เลือก',

  // -------------------------------------------------- delivery mock-up
  'Test mode. ': 'โหมดทดสอบ ',
  'This is how the messages would look — none of them were actually posted.':
    'นี่คือหน้าตาของข้อความ — แต่ยังไม่มีข้อความไหนถูกส่งออกจริง',
  'the image itself is not forwarded — only this text':
    'รูปภาพไม่ถูกส่งต่อ — ส่งเฉพาะข้อความนี้',
  'the picture is posted too, with this text as its caption':
    'รูปภาพถูกส่งไปด้วย โดยใช้ข้อความนี้เป็นคำบรรยายใต้ภาพ',
  'nothing to send — this message produces no LINE post':
    'ไม่มีอะไรให้ส่ง — ข้อความนี้ไม่สร้างโพสต์ใน LINE',
  'Oldest first. Each bubble is the exact text the bridge pushes — the same string, character for character. ':
    'เรียงจากเก่าไปใหม่ ทุกกล่องคือข้อความที่ระบบส่งออกจริง ตรงกันทุกตัวอักษร ',
  'The destination is a Telegram channel, so a photo is posted as the picture itself with this text as its caption.':
    'ปลายทางเป็นแชนแนล Telegram รูปภาพจึงถูกส่งเป็นรูปจริง โดยมีข้อความนี้เป็นคำบรรยายใต้ภาพ',
  'Everything is sent to LINE as a text message, so a Telegram photo arrives as ':
    'ทุกอย่างถูกส่งเข้า LINE เป็นข้อความล้วน รูปภาพจาก Telegram จึงมาถึงเป็น ',
  ' and the picture itself does not travel.': ' และตัวรูปภาพไม่ถูกส่งไปด้วย',
  ' An edit arrives as a new message prefixed ': ' ข้อความที่ถูกแก้ไขจะมาเป็นข้อความใหม่ นำหน้าด้วย ',
  '; it never replaces the one before it.': ' ไม่ได้ไปทับข้อความเดิม',

  // ------------------------------------------------------------- ranges
  'Today': 'วันนี้',
  'Yesterday': 'เมื่อวาน',
  '7 days': '7 วัน',
  '30 days': '30 วัน',
  'This week': 'สัปดาห์นี้',
  'This month': 'เดือนนี้',
  '3 months': '3 เดือน',
  '6 months': '6 เดือน',
  '1 year': '1 ปี',
  'This year': 'ปีนี้',
  'to': 'ถึง',
  'Points': 'Points',
  'Signal Performance': 'ผลงานสัญญาณ',
  'Signal Admin': 'ระบบผู้ดูแล',
  'Gold signals · results in points': 'สัญญาณทองคำ · ผลเป็น points',
  'operations · not visible to members': 'สำหรับผู้ดูแล · สมาชิกมองไม่เห็น',
  'Member dashboard →': 'หน้าสมาชิก →',
  'Sign out': 'ออกจากระบบ',
  'connecting…': 'กำลังเชื่อมต่อ…',

  // -------------------------------------------------- sentences with values
  // `{n}` placeholders are filled by t(text, {n: …}).
  'All figures in points · times in {tz}': 'ทุกตัวเลขเป็น points · เวลาโซน {tz}',
  '{decided} decided · {open} open': 'ตัดสินแล้ว {decided} · ยังเปิด {open}',
  '{w}W / {l}L': 'ชนะ {w} / แพ้ {l}',
  'no decided signals yet': 'ยังไม่มีสัญญาณที่ตัดสินผลแล้ว',
  'gross profit / gross loss': 'กำไรรวม / ขาดทุนรวม',
  'largest peak-to-trough drop': 'ระยะที่ตกจากยอดสูงสุดมากที่สุด',
  'average points per decided signal': 'points เฉลี่ยต่อสัญญาณที่ตัดสินแล้ว',
  'from the posted entry, SL and TP1': 'คำนวณจากราคาเข้า SL และ TP1 ที่ประกาศไว้',
  '{open} still open, {ambiguous} ambiguous (take profit and stop loss in the same candle), {cancelled} never filled. They stay listed under Signals.':
    'ยังเปิดอยู่ {open} · ตัดสินไม่ได้ {ambiguous} (ชนทั้ง TP และ SL ในแท่งเดียว) · ไม่ได้เข้าไม้ {cancelled} — ทั้งหมดยังแสดงอยู่ในหน้าสัญญาณ',
  'Each outcome below is taken from what the provider announced about its own trade — a message such as "90 Pips! Can secure as TP2" — and has not been checked against price history. They reflect what was posted in the group, not an independent measurement.':
    'ผลทุกรายการด้านล่างมาจากสิ่งที่ผู้แจกสัญญาณประกาศเองเกี่ยวกับไม้ของตัวเอง เช่นข้อความ "90 Pips! Can secure as TP2" และยังไม่ได้ถูกตรวจสอบกับราคาจริง ตัวเลขเหล่านี้สะท้อนสิ่งที่โพสต์ในกลุ่ม ไม่ใช่การวัดผลอิสระ',
  'This is how the messages would look — none of them were actually posted to LINE.':
    'นี่คือหน้าตาของข้อความ — แต่ยังไม่มีข้อความไหนถูกส่งเข้า LINE จริง',
  '{n} characters': '{n} ตัวอักษร',

  // ------------------------------------------------------- settings page
  'Saving rewrites the settings file and restarts the service, so what is running is always what is on disk. Leave a secret blank to keep the stored value.':
    'การบันทึกจะเขียนไฟล์ตั้งค่าใหม่แล้วรีสตาร์ตระบบ ค่าที่ใช้งานอยู่จึงตรงกับไฟล์เสมอ ช่องที่เป็นความลับถ้าเว้นว่างไว้ ระบบจะเก็บค่าเดิม',
  'Saved: {keys}. ': 'บันทึกแล้ว: {keys} ',
  'The service is restarting — give it about ten seconds, then reload this page.':
    'ระบบกำลังรีสตาร์ต รอประมาณสิบวินาทีแล้วรีเฟรชหน้านี้',
  'Which app the messages are posted into.': 'เลือกว่าจะส่งข้อความไปที่แอปไหน',
  'The channel to post into, e.g. @mychannel. Only used when the target is telegram.':
    'แชนแนลที่จะส่งเข้าไป เช่น @mychannel ใช้เมื่อเลือกปลายทางเป็น telegram เท่านั้น',
  'From my.telegram.org → API development tools.': 'เอามาจาก my.telegram.org → API development tools',
  'From the same page. Changing it needs a fresh sign-in.':
    'จากหน้าเดียวกัน ถ้าเปลี่ยนต้องล็อกอิน Telegram ใหม่',
  'The group the signals are read from, e.g. -1001234567890.':
    'กลุ่มที่ระบบอ่านสัญญาณ เช่น -1001234567890',
  'Messaging API → Channel access token (long-lived).':
    'จาก LINE console: Messaging API → Channel access token (long-lived)',
  'Starts with C for a group, R for a room, U for a person.':
    'ขึ้นต้นด้วย C คือกลุ่ม, R คือห้อง, U คือบุคคล',
  'Off stores messages without pushing them.': 'ปิด = เก็บข้อความไว้แต่ไม่ส่ง',
  'On is test mode: read and parse everything, post nothing.':
    'เปิด = โหมดทดสอบ อ่านและแปลทุกอย่าง แต่ไม่ส่งออก',
  'Whether an edited message is delivered marked EDITED.':
    'ข้อความที่ถูกแก้ไขจะถูกส่งพร้อมคำว่า EDITED หรือไม่',
  'price = checked against price history. message = the provider’s own reports.':
    'price = ตรวจสอบกับราคาจริง · message = ใช้ผลที่ผู้แจกสัญญาณรายงานเอง',
  'Twelve Data key. Only needed when the provider is twelvedata.':
    'คีย์ของ Twelve Data ใช้เฉพาะเมื่อเลือก provider เป็น twelvedata',
  'The unit statistics are reported in. 0.01 makes a $7 move read as 700.':
    'หน่วยที่ใช้รายงานสถิติ ถ้าตั้ง 0.01 การขยับ $7 จะแสดงเป็น 700',
  'What one pip is worth in price, for targets quoted as "TP: 50/100Pips".':
    'ค่าของ 1 pip ในหน่วยราคา ใช้กับเป้าหมายที่เขียนแบบ "TP: 50/100Pips"',
  'When one candle holds both the target and the stop.':
    'กรณีที่แท่งเทียนเดียวชนทั้ง TP และ SL',
  'Show members the archive of everything posted to LINE.':
    'ให้สมาชิกเห็นประวัติข้อความทั้งหมดที่ส่งเข้า LINE',

  // --------------------------------------------------------- methodology
  'How these numbers are produced': 'ตัวเลขเหล่านี้คำนวณมาอย่างไร',
  'This page exists so the performance figures can be checked rather than taken on trust. ':
    'หน้านี้มีไว้ให้ตรวจสอบตัวเลขได้จริง ไม่ใช่ให้เชื่อไปเฉยๆ ',
  'Rules': 'กฎที่ใช้',
  'Unit': 'หน่วย',
  'Point size': 'ขนาดของ 1 point',
  'Timezone': 'เขตเวลา',
  'Price source': 'แหล่งราคา',
  'Price provider': 'ผู้ให้บริการราคา',
  'Price timeframe': 'ไทม์เฟรมราคา',
  'Result source': 'ที่มาของผล',
  'Same-candle rule': 'กฎเมื่อชนทั้ง TP และ SL ในแท่งเดียว',
  'Result mode': 'โหมดการนับผล',
  'Entry fill window': 'ระยะเวลารอเข้าไม้',
  'Signal expiry': 'สัญญาณหมดอายุ',
  'Message parsers': 'ตัวอ่านข้อความ',
  'What is not shown': 'สิ่งที่ไม่แสดง',
  'What cannot happen': 'สิ่งที่ระบบทำไม่ได้',
  'A losing signal cannot be deleted or hidden; there is no delete route.':
    'สัญญาณที่แพ้ลบหรือซ่อนไม่ได้ ระบบไม่มีคำสั่งลบเลย',
  'Entry, stop and target cannot be rewritten after the fact to improve a result.':
    'ราคาเข้า SL และ TP แก้ย้อนหลังเพื่อให้ผลดูดีขึ้นไม่ได้',
  'Edit history cannot be removed; every version of every message is kept.':
    'ประวัติการแก้ไขลบไม่ได้ ทุกเวอร์ชันของทุกข้อความถูกเก็บไว้',
  'Risk disclaimer': 'คำเตือนความเสี่ยง',
  'Risk disclaimer. ': 'คำเตือนความเสี่ยง ',
  'These results are reported by the signal provider. ':
    'ผลเหล่านี้เป็นสิ่งที่ผู้แจกสัญญาณรายงานเอง ',
};

const DICT = { th: TH, en: {} };

let current = null;

export function lang() {
  if (current) return current;
  try {
    const stored = localStorage.getItem(STORE);
    if (stored && DICT[stored]) return (current = stored);
  } catch (_) {
    /* private window, or site data blocked */
  }
  return (current = 'th');   // Thai is what the members read
}

export function setLang(next) {
  if (!DICT[next]) return;
  current = next;
  try {
    localStorage.setItem(STORE, next);
  } catch (_) {
    /* the choice just will not survive a reload */
  }
  document.documentElement.lang = next;
}

/** Translate. Unknown strings fall through to the English they were written in. */
export function t(text, vars) {
  if (text === null || text === undefined) return text;
  const table = DICT[lang()];
  let out = (table && table[text]) || text;
  if (vars) {
    // `{name}` is replaced after translation, so a translator may reorder the
    // values inside a sentence without touching the code.
    out = out.replace(/\{(\w+)\}/g, (whole, key) => (key in vars ? String(vars[key]) : whole));
  }
  return out;
}

/** A language switch for the top bar. `onchange` is called after the change. */
export function languageSwitch(onchange) {
  const wrap = document.createElement('div');
  wrap.className = 'segmented lang-switch';
  for (const [code, label] of LANGUAGES) {
    const button = document.createElement('button');
    button.textContent = label;
    button.className = code === lang() ? 'active' : '';
    button.onclick = () => {
      if (code === lang()) return;
      setLang(code);
      onchange ? onchange(code) : location.reload();
    };
    wrap.append(button);
  }
  return wrap;
}

// Set it once at load so the page announces its own language to the browser.
document.documentElement.lang = lang();
