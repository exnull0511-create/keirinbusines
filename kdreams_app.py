"""
Kドリームス競輪スクレイピングアプリ
Streamlitを使用したWebアプリケーション
"""
import streamlit as st
import pandas as pd
from kdreams_scraper import KdreamsScraper
import io


def main():
    st.set_page_config(
        page_title="Kドリームス競輪データ取得",
        page_icon="🚴",
        layout="wide"
    )
    
    st.title("🚴 Kドリームス競輪データスクレイピング")
    st.markdown("**当日のS級レースデータを取得・CSV出力**")
    
    # セッション状態の初期化
    # バージョン番号を上げると古いスクレイパーインスタンスをリセットする
    SCRAPER_VERSION = "2"
    if 'scraper' not in st.session_state or st.session_state.get('scraper_version') != SCRAPER_VERSION:
        st.session_state.scraper = KdreamsScraper()
        st.session_state.scraper_version = SCRAPER_VERSION

    if 'race_data' not in st.session_state:
        st.session_state.race_data = None
    
    # サイドバー: レース選択
    st.sidebar.header("📋 レース選択")
    
    # 日付選択
    st.sidebar.markdown("### 📅 日付選択")
    date_option = st.sidebar.radio(
        "取得する日付:",
        options=["本日", "前日"],
        horizontal=True,
        key="date_option"
    )
    
    # 日付タイプを決定
    date_type = "today" if date_option == "本日" else "yesterday"
    
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"### 📍 {date_option}のレース")
    
    # レース一覧取得ボタン
    if st.sidebar.button(f"🔄  {date_option}の開催場一覧を取得", use_container_width=True):
        with st.spinner("開催場一覧を取得中..."):
            races = st.session_state.scraper.get_races(date_type)
            st.session_state.races = races
            st.session_state.current_date_type = date_type
            if races:
                venues = {}
                for race in races:
                    velodrome = race['velodrome']
                    if velodrome not in venues:
                        venues[velodrome] = []
                    venues[velodrome].append(race)
                st.session_state.venues = venues
                st.sidebar.success(f"✅ {len(venues)}場の開催場を取得")
            else:
                st.sidebar.error("❌ レースが見つかりませんでした")
    
    # 2段階選択: 開催場 → レース
    if 'venues' in st.session_state and st.session_state.venues:
        st.sidebar.markdown("### ステップ1: 開催場を選択")
        
        venue_options = []
        for velodrome, races in st.session_state.venues.items():
            grade = races[0]['grade']
            day = races[0].get('day', '')
            venue_options.append(f"{velodrome} ({grade}) {day}")
        
        selected_venue_idx = st.sidebar.selectbox(
            "開催場:",
            range(len(venue_options)),
            format_func=lambda x: venue_options[x],
            key="venue_select"
        )
        
        selected_venue_name = list(st.session_state.venues.keys())[selected_venue_idx]
        venue_info = st.session_state.venues[selected_venue_name][0]
        
        # 全レースURLを生成（1R-12R）
        all_races = st.session_state.scraper.get_all_races_from_venue(venue_info['url'])
        
        # 一括取得ボタン
        st.sidebar.markdown("---")
        if st.sidebar.button("📦 この開催場の全レースを一括取得", use_container_width=True, type="secondary"):
            with st.spinner(f"{selected_venue_name} の全レースデータを取得中... (2〜3分かかります)"):
                progress_container = st.empty()
                progress_bar = progress_container.progress(0)
                
                bulk_data = st.session_state.scraper.get_venue_all_data(
                    selected_venue_name,
                    venue_info['url']
                )
                bulk_data['grade'] = venue_info['grade']
                
                st.session_state.bulk_data = bulk_data
                st.session_state.race_data = None
                
                progress_bar.progress(100)
                st.rerun()
        
        st.sidebar.markdown("---")
        
        if all_races:
            st.sidebar.markdown("### ステップ2: レースを選択")
            
            race_options = [f"{r['name']}" for r in all_races]
            selected_race_idx = st.sidebar.selectbox(
                f"{selected_venue_name}のレース:",
                range(len(race_options)),
                format_func=lambda x: race_options[x],
                key="race_select"
            )
            
            selected_race = all_races[selected_race_idx]
            selected_race['velodrome'] = selected_venue_name
            selected_race['grade'] = venue_info['grade']
            selected_race['name'] = f"{selected_venue_name} {selected_race['name']}"
            
            st.sidebar.markdown("---")
            st.sidebar.markdown(f"**📍 選択中:** {selected_race['name']}")
            st.sidebar.markdown(f"**🏁 グレード:** {selected_race['grade']}")
            
            # データ取得ボタン（オッズ削除、ライン情報追加）
            if st.sidebar.button("📥 データを取得", use_container_width=True, type="primary"):
                with st.spinner("データを取得中... (20〜30秒かかります)"):
                    race_card = st.session_state.scraper.get_race_card(selected_race['url'])
                    race_results = st.session_state.scraper.get_race_results(selected_race['url'])
                    lines = st.session_state.scraper.get_race_lines(selected_race['url'])
                    lines_text = st.session_state.scraper.get_race_lines_text(selected_race['url'])
                
                st.session_state.race_data = {
                    'race_card': race_card,
                    'race_results': race_results,
                    'lines': lines,
                    'lines_text': lines_text,
                    'race_name': selected_race['name'],
                    'race_url': selected_race['url']
                }
                
                st.rerun()
    
    else:
        st.sidebar.info("👆 まず「本日のレース一覧を取得」ボタンを押してください")
    
    # ──────────────────────────────────────────────────────────
    # メインエリア: 一括取得データ表示
    # ──────────────────────────────────────────────────────────
    if 'bulk_data' in st.session_state and st.session_state.bulk_data:
        bulk_data = st.session_state.bulk_data
        
        st.header(f"📦 {bulk_data['venue_name']} ({bulk_data['grade']}) - 一括取得データ")
        
        # 統合Excelダウンロードボタン（上部に配置）
        st.markdown("### 📥 統合ダウンロード")
        
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            if not bulk_data['race_cards'].empty:
                bulk_data['race_cards'].to_excel(writer, sheet_name='出走表', index=False)
            if not bulk_data['lines_list'].empty:
                bulk_data['lines_list'].to_excel(writer, sheet_name='ライン情報', index=False)
            if not bulk_data['results_list'].empty:
                bulk_data['results_list'].to_excel(writer, sheet_name='レース結果', index=False)
        
        excel_data = excel_buffer.getvalue()
        
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.download_button(
                label="📊 Excelファイルをダウンロード（出走表・ライン・結果統合）",
                data=excel_data,
                file_name=f"{bulk_data['venue_name']}_全レースデータ.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary"
            )
        
        st.markdown("---")
        
        tab1, tab2, tab3 = st.tabs(["🏁 出走表（全レース）", "🔗 ライン情報（全レース）", "🏆 結果（全レース）"])
        
        with tab1:
            st.subheader("出走表データ（全レース統合）")
            if not bulk_data['race_cards'].empty:
                st.markdown(f"**取得データ数:** {len(bulk_data['race_cards'])}行")
                st.dataframe(bulk_data['race_cards'], use_container_width=True, height=500)
            else:
                st.warning("出走表データが取得できませんでした")
        
        with tab2:
            st.subheader("ライン情報（全レース統合）")
            if not bulk_data['lines_list'].empty:
                st.markdown(f"**取得ライン数:** {len(bulk_data['lines_list'])}件")
                st.dataframe(bulk_data['lines_list'], use_container_width=True, height=500)
            else:
                st.warning("ライン情報が取得できませんでした")
        
        with tab3:
            st.subheader("レース結果（全レース統合）")
            if not bulk_data['results_list'].empty:
                st.markdown(f"**取得データ数:** {len(bulk_data['results_list'])}行")
                st.dataframe(bulk_data['results_list'], use_container_width=True, height=500)
            else:
                st.info("レース結果がまだ確定していないか、データが取得できませんでした")
        
        st.markdown("---")
        col1, col2, col3 = st.columns([2, 1, 1])
        with col3:
            if st.button("🗑️ データをクリア", use_container_width=True):
                st.session_state.bulk_data = None
                st.rerun()
    
    # ──────────────────────────────────────────────────────────
    # メインエリア: 個別レース表示
    # ──────────────────────────────────────────────────────────
    if st.session_state.race_data:
        data = st.session_state.race_data
        
        st.header(f"📊 {data['race_name']} - データ")
        
        tab1, tab2, tab3 = st.tabs(["🏁 出走表", "🔗 ライン情報", "🏆 レース結果"])
        
        # タブ1: 出走表
        with tab1:
            st.subheader("出走表データ（19カラム）")
            if not data['race_card'].empty:
                st.markdown(f"**取得選手数:** {len(data['race_card'])}名")
                st.dataframe(data['race_card'], use_container_width=True, height=400)
                
                csv_buffer = io.StringIO()
                data['race_card'].to_csv(csv_buffer, index=False, encoding='utf-8-sig')
                csv_data = csv_buffer.getvalue()
                
                col1, col2 = st.columns(2)
                with col1:
                    st.download_button(
                        label="📥 CSVダウンロード",
                        data=csv_data,
                        file_name=f"{data['race_name']}_出走表.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
                with col2:
                    with st.expander("📋 表をコピー（CSV形式）"):
                        st.caption("下のボックス右上のコピーアイコンをクリックしてコピーできます")
                        st.code(csv_data, language="csv")
            else:
                st.warning("出走表データが取得できませんでした")
        
        # タブ2: ライン情報
        with tab2:
            st.subheader("ライン情報（並び予想）")
            lines = data.get('lines', [])
            lines_text = data.get('lines_text', '')
            
            if lines:
                st.markdown(f"**ライン予想:** `{lines_text}`")
                st.markdown("---")
                for ln in lines:
                    bibs_str = " → ".join(str(b) for b in ln['bibs'])
                    st.markdown(f"**ライン {ln['line']}:** {bibs_str}（{len(ln['bibs'])}車）")
                
                # DataFrameでも表示
                lines_df = pd.DataFrame([
                    {'ライン番号': ln['line'], '車番': '-'.join(str(b) for b in ln['bibs'])}
                    for ln in lines
                ])
                st.dataframe(lines_df, use_container_width=True)
                
                csv_buffer = io.StringIO()
                lines_df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
                st.download_button(
                    label="📥 ラインCSVダウンロード",
                    data=csv_buffer.getvalue(),
                    file_name=f"{data['race_name']}_ライン.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            else:
                st.info("ライン情報が取得できませんでした（レース前日は未掲載の場合があります）")
        
        # タブ3: レース結果
        with tab3:
            st.subheader("レース結果詳細")
            if not data['race_results'].empty:
                st.markdown(f"**出走選手数:** {len(data['race_results'])}名")
                st.dataframe(data['race_results'], use_container_width=True, height=350)
                
                csv_buffer = io.StringIO()
                data['race_results'].to_csv(csv_buffer, index=False, encoding='utf-8-sig')
                csv_data = csv_buffer.getvalue()
                
                col1, col2 = st.columns(2)
                with col1:
                    st.download_button(
                        label="📥 CSVダウンロード",
                        data=csv_data,
                        file_name=f"{data['race_name']}_結果.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
                with col2:
                    with st.expander("📋 表をコピー（CSV形式）"):
                        st.caption("下のボックス右上のコピーアイコンをクリックしてコピーできます")
                        st.code(csv_data, language=None)
            else:
                st.info("レース結果がまだ確定していないか、データが取得できませんでした")
        
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col2:
            if st.button("🗑️ データをクリア", use_container_width=True):
                st.session_state.race_data = None
                st.rerun()
    
    elif not ('bulk_data' in st.session_state and st.session_state.bulk_data):
        st.info("👈 サイドバーから開催場とレースを選択してデータを取得してください")
        
        with st.expander("📖 使い方ガイド"):
            st.markdown("""
            ## 🎯 2つの取得方法
            
            ### 方法1: レース個別取得（20〜30秒）
            1. サイドバーの「本日の開催場一覧を取得」ボタンをクリック
            2. ステップ1で開催場を選択
            3. ステップ2でレース（1R〜12R）を選択
            4. 「データを取得」ボタンをクリック
            
            **取得データ:**
            - 出走表: 19カラムの詳細データ
            - ライン情報: 並び予想（ライン番号・車番）
            - レース結果: 着順、車番、選手名、着差、上がり、決まり手、S/B
            
            ---
            
            ### 方法2: 開催場一括取得（2〜3分）⭐
            1. サイドバーの「本日の開催場一覧を取得」ボタンをクリック
            2. ステップ1で開催場を選択
            3. 「📦 この開催場の全レースを一括取得」ボタンをクリック
            4. 「📊 Excelファイルをダウンロード」で1つのファイル（3シート）を取得
            
            **Excelシート構成:**
            - 「出走表」シート: 全レースの出走表（レース列付き）
            - 「ライン情報」シート: 全レースのライン情報（レース列付き）
            - 「レース結果」シート: 全レースの結果（レース列付き）
            
            ---
            
            ### ⚠️ 注意事項
            - 一括取得は2〜3分程度かかります
            - サーバーに過度な負荷をかけないよう、連続実行は避けてください
            - Kドリームスのサイト構造変更により、データ取得が失敗する場合があります
            """)


if __name__ == "__main__":
    main()
