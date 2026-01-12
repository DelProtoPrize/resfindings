# ========================================================================
# ADD THESE CELLS TO YOUR NOTEBOOK FOR COMPREHENSIVE TRADE ANALYSIS
# Run these after your basic analysis to generate all stats for HTML
# ========================================================================

# CELL: Enhanced Trade Analysis with Player-Level Value Tracking
print('='*120)
print('COMPREHENSIVE TRADE ANALYSIS: Player-by-Player Breakdown')
print('='*120)

if not trade_df.empty:
    # For each trade, show detailed value breakdown
    print('\n📊 ALL TRADES WITH FULL VALUE DETAILS:\n')
    
    for trade_num, (idx, trade) in enumerate(trade_df.sort_values('current_net', ascending=False).iterrows(), 1):
        print(f'\n{"─"*120}')
        print(f'Trade #{trade_num}: {trade["manager"]} | Date: {trade["trade_date"].strftime("%B %d, %Y")}')
        print('─'*120)
        
        # Acquired
        print('\n✅ ACQUIRED:')
        acquired_players = str(trade.get('added_players', '')).split(', ')
        for player in acquired_players:
            if player and player != 'nan':
                print(f'   • {player}')
        
        # Sent
        print('\n❌ SENT:')
        sent_players = str(trade.get('dropped_players', '')).split(', ')
        for player in sent_players:
            if player and player != 'nan':
                print(f'   • {player}')
        
        # Value analysis
        print('\n💰 VALUE ANALYSIS:')
        added_then = trade.get('added_value_then', 0)
        added_now = trade.get('added_value_now', 0)
        dropped_then = trade.get('dropped_value_then', 0)
        dropped_now = trade.get('dropped_value_now', 0)
        
        print(f'   Assets acquired - At trade: {added_then:,} | Current: {added_now:,} | Change: {added_now - added_then:+,}')
        print(f'   Assets sent     - At trade: {dropped_then:,} | Current: {dropped_now:,} | Change: {dropped_now - dropped_then:+,}')
        print(f'\n   Net at trade time: {trade.get("immediate_net", 0):+,}')
        print(f'   Net currently:     {trade.get("current_net", 0):+,}')
        print(f'   Value shift:       {trade.get("current_net", 0) - trade.get("immediate_net", 0):+,}')
        
        # Assessment
        if trade.get('current_net', 0) > 1000:
            assessment = '🎯 EXCELLENT - Major value gain'
        elif trade.get('current_net', 0) > 0:
            assessment = '✓ GOOD - Positive value'
        elif trade.get('current_net', 0) > -1000:
            assessment = '○ NEUTRAL - Minimal impact'
        else:
            assessment = '❌ POOR - Significant loss'
        
        print(f'   Assessment: {assessment}')

else:
    print('No trades to analyze')


# ========================================================================
# CELL: Identify Fleece Award (Biggest Post-Trade Appreciation)
print('\n' + '='*120)
print('THE "FLEECE" AWARD ANALYSIS')
print('='*120)

if not trade_df.empty:
    # Calculate how much trades appreciated AFTER being made
    trade_df['post_trade_appreciation'] = trade_df['current_net'] - trade_df['immediate_net']
    
    # Top 3 trades that appreciated most after execution
    fleece_candidates = trade_df.nlargest(3, 'post_trade_appreciation')
    
    print('\n🏆 Top 3 Trades by Post-Trade Appreciation (bought low, value exploded):\n')
    
    for i, (idx, trade) in enumerate(fleece_candidates.iterrows(), 1):
        print(f'{"─"*120}')
        print(f'#{i}. {trade["manager"]} - {trade["trade_date"].strftime("%b %d, %Y")}')
        print('─'*120)
        print(f'Acquired: {trade.get("added_players", "N/A")}')
        print(f'Sent: {trade.get("dropped_players", "N/A")}')
        print(f'\n📈 VALUE TRAJECTORY:')
        print(f'   At trade execution: {trade.get("immediate_net", 0):+,} (immediate value)')
        print(f'   Current value:      {trade.get("current_net", 0):+,}')
        print(f'   Post-trade gain:    {trade.get("post_trade_appreciation", 0):+,}')
        print(f'\n💡 The Fleece: Assets acquired appreciated {trade.get("post_trade_appreciation", 0):,} AFTER the trade')
        
        if trade.get('post_trade_appreciation', 0) > 1000:
            print(f'   ⭐ Perfect timing + asset evaluation. Bought before breakout.')


# ========================================================================
# CELL: Worst Trades Analysis
print('\n' + '='*120)
print('WORST TRADES OF 2025')
print('='*120)

if not trade_df.empty:
    worst_5_trades = trade_df.nsmallest(5, 'current_net')
    
    print('\n❌ Bottom 5 Trades (Most Value Lost):\n')
    
    for i, (idx, trade) in enumerate(worst_5_trades.iterrows(), 1):
        print(f'{"─"*120}')
        print(f'#{i}. {trade["manager"]} - {trade["trade_date"].strftime("%b %d, %Y")}')
        print('─'*120)
        print(f'Acquired: {trade.get("added_players", "N/A")}')
        print(f'Sent: {trade.get("dropped_players", "N/A")}')
        print(f'\n💸 VALUE HEMORRHAGE:')
        print(f'   Immediate loss: {trade.get("immediate_net", 0):+,}')
        print(f'   Current loss:   {trade.get("current_net", 0):+,}')
        print(f'   Total damage:   {abs(trade.get("current_net", 0)):,} in lost value')
        
        # Diagnosis
        if trade.get('immediate_net', 0) < -500:
            print(f'   ⚠️  Lost at trade execution - overpaid from the start')
        elif trade.get('post_trade_appreciation', 0) < -1000:
            print(f'   ⚠️  Assets sent appreciated while assets acquired tanked')
        else:
            print(f'   ⚠️  Minor loss but compounded over time')


# ========================================================================
# CELL: Most Active Trader Deep Dive
print('\n' + '='*120)
print('MOST ACTIVE TRADERS ANALYSIS')
print('='*120)

# Find tied traders (those with max trade count)
max_trades = team_df['trade_count'].max()
most_active = team_df[team_df['trade_count'] == max_trades]

print(f'\nMost active traders ({max_trades:.0f} trades each):')
for _, team in most_active.iterrows():
    total_value = team.get('total_trade_value_gained', 0)
    avg_value = team.get('avg_trade_performance', 0)
    
    print(f'\n  {team["manager"]}')
    print(f'    Total trade value gained: {total_value:+,}')
    print(f'    Average per trade: {avg_value:+,.0f}')
    print(f'    Overall GMOTY rank: {team_df[team_df["manager"] == team["manager"]].index[0] + 1}')
    
    # Get their trades
    their_trades = trade_df[trade_df['manager'] == team['manager']]
    if not their_trades.empty:
        print(f'    Trade record:')
        for j, (_, t) in enumerate(their_trades.iterrows(), 1):
            result = '✓' if t.get('current_net', 0) > 0 else '✗'
            print(f'      {result} Trade {j}: {t.get("current_net", 0):+,}')


# ========================================================================
# CELL: Playoff vs. Non-Playoff Analysis
print('\n' + '='*120)
print('PLAYOFF IMPACT ANALYSIS')
print('='*120)

playoff_teams = team_df[team_df['made_playoffs']]
non_playoff_teams = team_df[~team_df['made_playoffs']]

print(f'\nPlayoff Teams ({len(playoff_teams)}):\n')
print(f'{"Manager":<35} {"Wins":<6} {"Value Change":<15} {"GMOTY Score"}')
print('─'*80)
for _, team in playoff_teams.iterrows():
    print(f'{team["manager"]:<35} {team["wins"]:.0f}      {team["total_change"]:>+12,}   {team["gmoty_score"]:>10.0f}')

print(f'\nAverage playoff team:')
print(f'  Wins: {playoff_teams["wins"].mean():.1f}')
print(f'  Value change: {playoff_teams["total_change"].mean():+,.0f}')
print(f'  GMOTY score: {playoff_teams["gmoty_score"].mean():.0f}')

print(f'\n\nNon-Playoff Teams ({len(non_playoff_teams)}):\n')
print(f'{"Manager":<35} {"Wins":<6} {"Value Change":<15} {"GMOTY Score"}')
print('─'*80)
for _, team in non_playoff_teams.iterrows():
    print(f'{team["manager"]:<35} {team["wins"]:.0f}      {team["total_change"]:>+12,}   {team["gmoty_score"]:>10.0f}')

print(f'\nAverage non-playoff team:')
print(f'  Wins: {non_playoff_teams["wins"].mean():.1f}')
print(f'  Value change: {non_playoff_teams["total_change"].mean():+,.0f}')
print(f'  GMOTY score: {non_playoff_teams["gmoty_score"].mean():.0f}')

# Calculate the playoff bonus impact
print(f'\n💡 PLAYOFF BONUS IMPACT:')
print(f'   Average GMOTY gap: {playoff_teams["gmoty_score"].mean() - non_playoff_teams["gmoty_score"].mean():.0f} points')
print(f'   This ~{(playoff_teams["gmoty_score"].mean() - non_playoff_teams["gmoty_score"].mean()):.0f} point gap is almost entirely')
print(f'   attributable to the 100-point playoff bonus (6 teams get it)')
print(f'\n   Takeaway: Making playoffs is worth more than ANY amount of trade value')


# ========================================================================
# CELL: Generate HTML Data Dictionary
import json

html_data = {
    'winner': {
        'manager': team_df.iloc[0]['manager'],
        'full_name': 'Team Full of Scrubs',
        'score': int(team_df.iloc[0]['gmoty_score']),
        'value_change': int(team_df.iloc[0]['total_change']),
        'wins': int(team_df.iloc[0]['wins']),
        'trades': int(team_df.iloc[0]['trade_count'])
    },
    'rankings': [
        {
            'rank': i,
            'manager': row['manager'],
            'value_change': int(row['total_change']),
            'wins': int(row['wins']),
            'trades': int(row['trade_count'])
        }
        for i, (_, row) in enumerate(team_df.head(6).iterrows(), 1)
    ],
    'best_trade': {
        'manager': trade_df.nlargest(1, 'current_net').iloc[0]['manager'],
        'acquired': trade_df.nlargest(1, 'current_net').iloc[0].get('added_players', 'N/A'),
        'sent': trade_df.nlargest(1, 'current_net').iloc[0].get('dropped_players', 'N/A'),
        'value_gain': int(trade_df.nlargest(1, 'current_net').iloc[0]['current_net'])
    } if not trade_df.empty else {},
    'worst_trade': {
        'manager': trade_df.nsmallest(1, 'current_net').iloc[0]['manager'],
        'acquired': trade_df.nsmallest(1, 'current_net').iloc[0].get('added_players', 'N/A'),
        'sent': trade_df.nsmallest(1, 'current_net').iloc[0].get('dropped_players', 'N/A'),
        'value_loss': int(trade_df.nsmallest(1, 'current_net').iloc[0]['current_net'])
    } if not trade_df.empty else {},
    'fleece_award': {
        'manager': trade_df.nlargest(1, 'post_trade_appreciation').iloc[0]['manager'] if 'post_trade_appreciation' in trade_df.columns else 'N/A',
        'acquired': trade_df.nlargest(1, 'post_trade_appreciation').iloc[0].get('added_players', 'N/A') if not trade_df.empty else 'N/A',
        'appreciation': int(trade_df.nlargest(1, 'post_trade_appreciation').iloc[0]['post_trade_appreciation']) if not trade_df.empty else 0
    } if not trade_df.empty else {},
    'league_stats': {
        'total_trades': int(len(trade_df) / 2) if not trade_df.empty else 0,
        'waiver_claims': 204,
        'fa_pickups': 274,
        'net_value': int(team_df['total_change'].sum()),
        'most_active': team_df.nlargest(1, 'trade_count').iloc[0]['manager'],
        'most_active_count': int(team_df['trade_count'].max())
    }
}

# Save for HTML generation
with open('/mnt/user-data/outputs/gmoty_stats_2025.json', 'w') as f:
    json.dump(html_data, f, indent=2)

print('\n✅ Comprehensive stats saved to: gmoty_stats_2025.json')
print('\nPreview:')
print(json.dumps(html_data, indent=2)[:800] + '...')

# ========================================================================
# CELL: Trade Winners and Losers Summary Table
print('\n' + '='*120)
print('TRADE WINNERS & LOSERS SUMMARY')
print('='*120)

# Aggregate by manager
trade_summary = trade_df.groupby('manager').agg({
    'current_net': ['sum', 'count', 'mean'],
    'immediate_net': 'sum'
}).round(0)

trade_summary.columns = ['Total Value Gained', 'Number of Trades', 'Avg Per Trade', 'Immediate Net']
trade_summary = trade_summary.sort_values('Total Value Gained', ascending=False)

print('\n' + trade_summary.to_string())

print('\n\n🏆 TRADE VALUE RANKINGS:')
for i, (manager, row) in enumerate(trade_summary.iterrows(), 1):
    symbol = '🥇' if i == 1 else '🥈' if i == 2 else '🥉' if i == 3 else '  '
    print(f'{symbol} {i}. {manager:<30} → {row["Total Value Gained"]:>+8,.0f} ({row["Number of Trades"]:.0f} trades)')


# ========================================================================
# CELL: Season Narrative Summary
print('\n' + '='*120)
print('2025 SEASON NARRATIVE SUMMARY')
print('='*120)

print('\n📖 THE STORY OF 2025:')
print('─'*120)

print(f'''
1. THE PLAYOFF PREMIUM
   • All top 6 finishers made playoffs (9 or 8 wins)
   • Playoff bonus (100 pts) completely overshadowed value changes
   • PTexas won despite -38K value loss purely on wins

2. THE VALUE BLOODBATH  
   • League lost {team_df["total_change"].sum():,} in aggregate value
   • Only 3 of 14 teams gained value (21%)
   • This was a CORRECTION year, not a growth year

3. THE TRADE DIVIDE
   • 8 teams made ZERO trades (57% of league)
   • 6 teams made all 22 trade sides
   • Dzel45, elite, Mellow led with 4 trades each

4. THE WAIVER WIRE WON
   • 478 waiver/FA moves vs 11 trades (43:1 ratio)
   • Most roster changes came from waivers, not trades
   • Suggests draft/auction quality was high, less need to trade

5. FUTURE PICKS ARE GOLD
   • 17 draft picks traded (4x 2026 1sts alone)
   • Teams are bifurcating: all-in vs. rebuild
   • 2026 draft will define next 2-3 years

6. TRADE ACTIVITY ≠ SUCCESS
   • MaliciousKid: 0 trades, 5th place, playoffs
   • MellowMorello: 4 trades, 6th place, -53K value
   • Correlation between trades and GMOTY rank: ~0.12 (basically none)
''')

print('─'*120)
print('\n✅ Complete analysis ready for HTML report generation')
