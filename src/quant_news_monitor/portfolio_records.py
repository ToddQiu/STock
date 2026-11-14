"""Manual meeting and IPO execution records, scoped to one portfolio."""
from datetime import date, datetime, timezone
import math
import uuid

MEETING_FIELDS = {'name': '会议名称', 'kind': '会议类型', 'date': '会议日期',
                  'minutes': '会议纪要', 'conclusion': '会议结论', 'research': '调研讨论', 'stocks': '个股讨论'}
EXECUTION_NUMBERS = ('target_value', 'simulated_value', 'average20_value', 'quota')


def text(value, label, limit=6000):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{label}须为不超过{limit}字的文字')
    return value.strip()


def number(value, label):
    if value in ('', None):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label}须为有效数字') from None
    if isinstance(value, bool) or not math.isfinite(result) or not 0 <= result <= 1e15:
        raise ValueError(f'{label}须为非负数')
    return result


def estimate_quota(average20_value):
    """Apply the workbook's SH/SZ estimate to a recorded market value."""
    average = number(average20_value, '实际20日均市值')
    if average is None:
        return None
    return 0 if average < 10000 else math.floor(average / 5000) * 500


def snapshot(account):
    return {'meetings': account.get('meetings', []), 'execution': account.get('execution', {})}


def update(account, values):
    action = values.get('action')
    stamp = datetime.now(timezone.utc).isoformat()
    if action in ('meeting_save', 'meeting_delete'):
        rows = account.setdefault('meetings', [])
        identifier = values.get('id')
        previous = next((row for row in rows if row['id'] == identifier), None)
        if identifier and previous is None:
            raise ValueError('例会记录不存在，请重新打开')
        if action == 'meeting_delete':
            if previous is None:
                raise ValueError('请选择要删除的例会')
            rows.remove(previous)
        else:
            row = {key: text(values.get(key, ''), label, 120 if key in ('name', 'kind') else 6000)
                   for key, label in MEETING_FIELDS.items()}
            if not row['name']:
                raise ValueError('请填写会议名称')
            try:
                if row['date'] and date.fromisoformat(row['date']).isoformat() != row['date']:
                    raise ValueError()
            except ValueError:
                raise ValueError('会议日期无效') from None
            row.update(id=identifier or uuid.uuid4().hex, updated_at=stamp,
                       created_at=previous['created_at'] if previous else stamp)
            if previous:
                rows[rows.index(previous)] = row
            else:
                rows.append(row)
    elif action == 'execution_save':
        source = values.get('execution')
        if not isinstance(source, dict):
            raise ValueError('打新记录格式无效')
        result = {'budget': number(source.get('budget'), '打新资金上限'),
                  'notes': text(source.get('notes', ''), '执行说明'), 'updated_at': stamp, 'markets': {}}
        markets = source.get('markets') or {}
        if not isinstance(markets, dict):
            raise ValueError('市场记录格式无效')
        for market in ('SH', 'SZ'):
            item = markets.get(market) or {}
            if not isinstance(item, dict):
                raise ValueError('市场记录格式无效')
            row = {key: number(item.get(key), '市值或额度') for key in EXECUTION_NUMBERS}
            if row['quota'] is not None and not row['quota'].is_integer():
                raise ValueError('额度（股）须为整数')
            row.update(permission=text(item.get('permission', ''), '主板权限', 120),
                       status=text(item.get('status', ''), '当前状态', 500))
            result['markets'][market] = row
        account['execution'] = result
    else:
        raise ValueError('组合记录操作无效')
    return snapshot(account)
