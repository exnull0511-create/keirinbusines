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
    if 'scraper' not in st.session_state:
        st.session_state.scraper = KdreamsScraper()
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
            st.session_state.current_date_type = date_type  # 選択した日付タイプを保存
            if races:
                # 開催場ごとにグループ化
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
        
        # 開催場リストを作成（Grade情報付き）
        venue_options = []
        for velodrome, races in st.session_state.venues.items():
            grade = races[0]['grade']  # 最初のレースのGradeを使用
            day = races[0].get('day', '')
            venue_options.append(f"{velodrome} ({grade}) {day}")
        
        selected_venue_idx = st.sidebar.selectbox(
            "開催場:",
            range(len(venue_options)),
            format_func=lambda x: venue_options[x],
            key="venue_select"
        )
        
        # 選択した開催場の全レース（1R-12R）を生成
        selected_venue_name = list(st.session_state.venues.keys())[selected_venue_idx]
        venue_info = st.session_state.venues[selected_venue_name][0]  # 開催場情報
        
        
        # 全レースURLを生成（1R-12R）
        all_races = st.session_state.scraper.get_all_races_from_venue(venue_info['url'])
        
        # 一括取得ボタン
        st.sidebar.markdown("---")
        if st.sidebar.button("📦 この開催場の全レースを一括取得", use_container_width=True, type="secondary"):
            with st.spinner(f"{selected_venue_name} の全レースデータを取得中... (2〜3分かかります)"):
                # プログレスバー用のコンテナを作成
                progress_container = st.empty()
                progress_bar = progress_container.progress(0)
                
                # 一括取得を実行
                bulk_data = st.session_state.scraper.get_venue_all_data(
                    selected_venue_name,
                    venue_info['url']
                )
                
                # グレード情報を追加
                bulk_data['grade'] = venue_info['grade']
                
                # セッションに保存
                st.session_state.bulk_data = bulk_data
                st.session_state.race_data = None  # 個別データをクリア
                
                progress_bar.progress(100)
                st.rerun()
        
        st.sidebar.markdown("---")

        
        if all_races:
            st.sidebar.markdown("### ステップ2: レースを選択")
            
            # レース選択ドロップダウン (1R-12R)
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
            
            # データ取得ボタン
            if st.sidebar.button("📥 データを取得", use_container_width=True, type="primary"):
                with st.spinner("データを取得中... (20〜30秒かかります)"):
                    race_card = st.session_state.scraper.get_race_card(selected_race['url'])
                    race_results = st.session_state.scraper.get_race_results(selected_race['url'])
                    odds_data = st.session_state.scraper.get_odds(selected_race['url'], 'popular')
                
                st.session_state.race_data = {
                    'race_card': race_card,
                    'race_results': race_results,
                    'odds': odds_data,
                    'race_name': selected_race['name'],
                    'race_url': selected_race['url']
                }
                
                st.rerun()
    
    else:
        st.sidebar.info("👆 まず「本日のレース一覧を取得」ボタンを押してください")
    
    # メインエリア: 一括取得データ表示
    if 'bulk_data' in st.session_state and st.session_state.bulk_data:
        bulk_data = st.session_state.bulk_data
        
        st.header(f"📦 {bulk_data['venue_name']} ({bulk_data['grade']}) - 一括取得データ")
        
        # 統合Excelダウンロードボタン（上部に配置）
        st.markdown("### 📥 統合ダウンロード")
        
        # Excelファイル生成
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            # 出走表シート
            if not bulk_data['race_cards'].empty:
                bulk_data['race_cards'].to_excel(writer, sheet_name='出走表', index=False)
            
            # オッズシート
            if not bulk_data['odds_list'].empty:
                bulk_data['odds_list'].to_excel(writer, sheet_name='オッズ', index=False)
            
            # 結果シート
            if not bulk_data['results_list'].empty:
                bulk_data['results_list'].to_excel(writer, sheet_name='レース結果', index=False)
        
        excel_data = excel_buffer.getvalue()
        
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.download_button(
                label="📊 Excelファイルをダウンロード（出走表・オッズ・結果統合）",
                data=excel_data,
                file_name=f"{bulk_data['venue_name']}_全レースデータ.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary"
            )
        
        st.markdown("---")
        
        # タブで切り替え
        tab1, tab2, tab3 = st.tabs(["🏁 出走表（全レース）", "💰 オッズ（全レース）", "🏆 結果（全レース）"])
        
        # タブ1: 出走表
        with tab1:
            st.subheader("出走表データ（全レース統合）")
            if not bulk_data['race_cards'].empty:
                st.markdown(f"**取得データ数:** {len(bulk_data['race_cards'])}行")
                st.dataframe(bulk_data['race_cards'], use_container_width=True, height=500)
            else:
                st.warning("出走表データが取得できませんでした")
        
        # タブ2: オッズ
        with tab2:
            st.subheader("オッズデータ（全レース統合）")
            if not bulk_data['odds_list'].empty:
                st.markdown(f"**取得データ数:** {len(bulk_data['odds_list'])}行")
                st.dataframe(bulk_data['odds_list'], use_container_width=True, height=500)
            else:
                st.warning("オッズデータが取得できませんでした")
        
        # タブ3: 結果
        with tab3:
            st.subheader("レース結果（全レース統合）")
            if not bulk_data['results_list'].empty:
                st.markdown(f"**取得データ数:** {len(bulk_data['results_list'])}行")
                st.dataframe(bulk_data['results_list'], use_container_width=True, height=500)
            else:
                st.info("レース結果がまだ確定していないか、データが取得できませんでした")
        
        # クリアボタン
        st.markdown("---")
        col1, col2, col3 = st.columns([2, 1, 1])
        with col3:
            if st.button("🗑️ データをクリア", use_container_width=True):
                st.session_state.bulk_data = None
                st.rerun()
    
    # メインエリア: データ表示
    if st.session_state.race_data:
        data = st.session_state.race_data
        
        st.header(f"📊 {data['race_name']} - データ")
        
        # タブで切り替え
        tab1, tab2, tab3 = st.tabs(["🏁 出走表", "🏆 レース結果", "💰 オッズ (人気順)"])
        
        # タブ1: 出走表
        with tab1:
            st.subheader("出走表データ（19カラム）")
            if not data['race_card'].empty:
                st.markdown(f"**取得選手数:** {len(data['race_card'])}名")
                st.dataframe(data['race_card'], use_container_width=True, height=400)
                
                # CSVダウンロードとコピー用データ準備
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
                
                # コピー用のCSV表示（コピーボタン付き）
                with col2:
                    with st.expander("📋 表をコピー（CSV形式）"):
                        st.caption("下のボックス右上のコピーアイコンをクリックしてコピーできます")
                        st.code(csv_data, language="csv")
            else:
                st.warning("出走表データが取得できませんでした")
        
        # タブ2: レース結果
        with tab2:
            st.subheader("レース結果詳細")
            if not data['race_results'].empty:
                st.markdown(f"**出走選手数:** {len(data['race_results'])}名")
                st.dataframe(data['race_results'], use_container_width=True, height=350)
                
                # CSVダウンロードとコピー用データ準備
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
                
                # コピー用のCSV表示（コピーボタン付き）
                with col2:
                    with st.expander("📋 表をコピー（CSV形式）"):
                        st.caption("下のボックス右上のコピーアイコンをクリックしてコピーできます")
                        st.code(csv_data, language=None)
            else:
                st.info("レース結果がまだ確定していないか、データが取得できませんでした")
        
        # タブ3: オッズ（人気順）
        with tab3:
            st.subheader("オッズデータ（人気順 50通り）")
            if 'odds' in data and not data['odds'].empty:
                st.markdown(f"**取得オッズ数:** {len(data['odds'])}通り")
                st.dataframe(data['odds'], use_container_width=True, height=400)
                
                # CSVダウンロードとコピー用データ準備
                csv_buffer = io.StringIO()
                data['odds'].to_csv(csv_buffer, index=False, encoding='utf-8-sig')
                csv_data = csv_buffer.getvalue()
                
                col1, col2 = st.columns(2)
                with col1:
                    st.download_button(
                        label="📥 CSVダウンロード",
                        data=csv_data,
                        file_name=f"{data['race_name']}_オッズ.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
                
                # コピー用のCSV表示（コピーボタン付き）
                with col2:
                    with st.expander("📋 表をコピー（CSV形式）"):
                        st.caption("下のボックス右上のコピーアイコンをクリックしてコピーできます")
                        st.code(csv_data, language="csv")
            else:
                st.warning("⚠️ オッズデータが取得できませんでした。オッズページがJavaScriptで動的に読み込まれる場合、通常のHTTPリクエストでは取得できないことがあります。")
        
        # 全データ統合ダウンロード
        st.markdown("---")
        st.subheader("📦 統合ダウンロード")
        
        col1, col2 = st.columns(2)
        
        with col1:
            # すべてをZIPにまとめる（オプション）
            st.info("各データを個別にダウンロードしてください")
        
        with col2:
            # クリアボタン
            if st.button("🗑️ データをクリア", use_container_width=True):
                st.session_state.race_data = None
                st.rerun()
    
    else:
        st.info("👈 サイドバーから開催場とレースを選択してデータを取得してください")
        
        # 使い方ガイド
        with st.expander("📖 使い方ガイド"):
            st.markdown("""
            ## 🎯 2つの取得方法
            
            ### 方法1: レース個別取得
            1つのレースのデータを素早く取得したい場合
            
            **手順:**
            1. サイドバーの「本日の開催場一覧を取得」ボタンをクリック
            2. ステップ1で開催場を選択
            3. ステップ2でレースを選択（1R〜12R）
            4. 「データを取得」ボタンをクリック（20〜30秒）
            
            **取得データ:**
            - 出走表: 19カラムの詳細データ
            - レース結果: 着順、車番、選手名、着差、上がり、決まり手、S/B
            - オッズ（人気順）: 3連単オッズ 人気順50通り
            
            ---
            
            ### 方法2: 開催場一括取得 ⭐NEW
            開催場の全レース（1R〜12R）のデータをまとめて取得したい場合
            
            **手順:**
            1. サイドバーの「本日の開催場一覧を取得」ボタンをクリック
            2. ステップ1で開催場を選択
            3. 「📦 この開催場の全レースを一括取得」ボタンをクリック（2〜3分）
            
            **取得データ:**
            - 出走表（全レース統合）: 全レースの出走表データ（レース列付き）
            - オッズ（全レース統合）: 全レースのオッズデータ（レース列付き）
            - 結果（全レース統合）: 全レースの結果データ（レース列付き）
            
            **ダウンロード:**
            1つのExcelファイルをダウンロードできます。ファイルには3つのシートが含まれます:
            - 「出走表」シート: 全レースの出走表データ（レース列付き）
            - 「オッズ」シート: 全レースのオッズデータ（レース列付き）
            - 「レース結果」シート: 全レースの結果データ（レース列付き）
            
            ---
            
            ### ⚠️ 注意事項
            - 一括取得は2〜3分程度かかります（12レース × 3種類のデータ）
            - ダウンロードされるファイルはExcel形式（.xlsx）です
            - サーバーに過度な負荷をかけないよう、連続実行は避けてください
            - Kドリームスのサイト構造変更により、データ取得が失敗する場合があります
            """)


if __name__ == "__main__":
    main()
