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
    
    st.sidebar.markdown("### オプション1: 当日のレース")
    
    # レース一覧取得ボタン
    if st.sidebar.button("🔄 本日の開催場一覧を取得", use_container_width=True):
        with st.spinner("開催場一覧を取得中..."):
            races = st.session_state.scraper.get_todays_races()
            st.session_state.races = races
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
        st.info("👈 サイドバーからレースを選択してデータを取得してください")
        
        # 使い方ガイド
        with st.expander("📖 使い方ガイド"):
            st.markdown("""
            ### ステップ1: レース一覧を取得
            サイドバーの「本日のレース一覧を取得」ボタンをクリックして、当日のS級レース一覧を取得します。
            
            ### ステップ2: レースを選択
            ドロップダウンから取得したいレースを選択します。
            
            ### ステップ3: データを取得
            「データを取得」ボタンをクリックすると、以下のデータがスクレイピングされます：
            - **出走表**: 19カラムの詳細データ  
            - **レース結果**: 着順、車番、選手名、着差、上がり、決まり手、S/B
            - **オッズ（人気順）**: 3連単オッズ 人気順50通り（順位、組み合わせ、オッズ）
            
            ### ステップ4: データのダウンロード
            各タブでデータをプレビュー確認し、CSVファイルとしてダウンロードまたはコピーできます。
            
            ---
            
            ### ⚠️ 注意事項
            - スクレイピングには20〜30秒程度かかります
            - サーバーに過度な負荷をかけないよう、連続実行は避けてください
            - Kドリームスのサイト構造変更により、データ取得が失敗する場合があります
            """)


if __name__ == "__main__":
    main()
