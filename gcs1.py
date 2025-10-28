
# feature1_page.py
from dash import dcc, html

def build_feature1_layout():
    return html.Div(
        style={"maxWidth": "1250px", "margin": "0 auto", "fontFamily": "system-ui, Arial"},
        children=[
            html.H1("GCS 화면 통합", style={"textAlign": "center", "marginBottom": "20px"}),
            dcc.Tabs([
                dcc.Tab(label='Mission Planner', children=[
                    html.Div([
                        html.Iframe(
                            src="http://localhost:10000",
                            style={"width": "1200px", "height": "800px", "border": "none"}
                        )
                    ], style={"textAlign": "center"})
                ]),
                dcc.Tab(label='QGroundControl', children=[
                    html.Div([
                        html.Iframe(
                            src="http://localhost:10100",
                            style={"width": "1200px", "height": "800px", "border": "none"}
                        )
                    ], style={"textAlign": "center"})
                ])
            ])
        ]
    )

def build_dash_home_layout():
    return html.Div([html.H1("기존 대시보드")])
